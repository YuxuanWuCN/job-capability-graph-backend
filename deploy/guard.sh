#!/usr/bin/env bash
# guard.sh — 岗智融析生产服务器的唯一变更入口
# 用法：bash guard.sh <command> [args] [--yes]
# 设计原则：只读命令随手可用；任何写操作必须显式 --yes 且自带回滚。
set -uo pipefail

ROOT="${JOB_GRAPH_ROOT:-/opt/job-graph}"
DIST_DIR="${JOB_GRAPH_DIST:-/var/www/job-graph/dist}"
BASE="http://127.0.0.1:8080"
IMAGE="job-graph-backend"
MIN_AVAIL_MB=1500

C_OK=0
COMPOSE=(docker compose -f "$ROOT/compose.yaml" -f "$ROOT/deploy/compose.prod.yaml")

say()  { printf '[guard] %s\n' "$*"; }
warn() { printf '[guard][WARN] %s\n' "$*" >&2; }
die()  { printf '[guard][ABORT] %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "需要 root：sudo bash $0 $*"; }
need_yes()  { [ "${CONFIRM:-0}" = "1" ] || die "这是写操作，需在命令末尾加 --yes"; }
need_cmd()  { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-8}" "$1"; }
avail_mb()  { free -m | awk '/^Mem:/{print $7}'; }

wait_ready() {
  local i code
  for i in $(seq 1 "${1:-40}"); do
    code=$(http_code "$BASE/health/ready" 5)
    if [ "$code" = "200" ]; then say "ready=200（第 ${i} 次探测）"; return 0; fi
    sleep 3
  done
  code=$(http_code "$BASE/health/ready" 5)
  warn "等待超时，最后一次 ready=$code"
  return 1
}

# ---------- 只读 ----------
cmd_status() {
  need_cmd docker
  echo "===== 时间 ====="; date '+%F %T %Z'
  echo "===== 内存 ====="; free -m | head -2
  echo "===== 磁盘 ====="; df -h / /var/lib/docker 2>/dev/null | tail -2
  echo "===== 容器 ====="
  docker ps --all --format '{{.Names}}\t{{.Status}}' | grep '^job-graph-' || warn "未发现 job-graph-* 容器"
  echo "===== 内存上限 ====="
  for c in $(docker ps --format '{{.Names}}' | grep '^job-graph-'); do
    printf '%s\t%s\n' "$c" "$(docker inspect -f '{{.HostConfig.Memory}}' "$c")"
  done
  echo "===== 健康 ====="
  printf 'live=%s ready=%s home=%s guest=%s\n' \
    "$(http_code "$BASE/health/live")" \
    "$(http_code "$BASE/health/ready")" \
    "$(http_code "$BASE/")" \
    "$(curl -s -o /dev/null -w '%{http_code}' -X POST --max-time 8 "$BASE/api/v1/auth/guest")"
  echo "===== 定时任务 ====="; crontab -l 2>/dev/null | grep -v '^#' || true
}

cmd_health() {
  echo "--- /health/live ---";  curl -s --max-time 8 "$BASE/health/live";  echo
  echo "--- /health/ready ---"; curl -s --max-time 10 "$BASE/health/ready"; echo
}

cmd_logs() {
  local svc="${1:-api}" lines="${2:-200}" name
  case "$svc" in
    api|worker|scheduler|postgres|redis|neo4j|migrate|warmup)
      name="job-graph-${svc}-1" ;;
    *) name="$svc" ;;
  esac
  docker ps --all --format '{{.Names}}' | grep -qx "$name" || die "没有容器 $name"
  docker logs --tail "$lines" "$name" 2>&1
}

# ---------- 写 ----------
cmd_restart_api() {
  need_root; need_yes
  docker restart job-graph-api-1 >/dev/null || die "重启失败"
  wait_ready 20 || die "重启后未恢复，请立刻看 logs api"
  say "api 已重启并恢复"
}

cmd_safe_rebuild() {
  need_root; need_yes; need_cmd docker
  local avail prev
  avail=$(avail_mb)
  [ "$avail" -ge "$MIN_AVAIL_MB" ] || die "可用内存 ${avail}M < ${MIN_AVAIL_MB}M，拒绝构建（构建会触发整机 hang）"
  say "可用内存 ${avail}M，满足门槛，开始安全重建"
  prev="${IMAGE}:prev-$(date +%Y%m%d-%H%M%S)"

  ( cd "$ROOT" || exit 1
    docker compose -f compose.yaml -f deploy/compose.prod.yaml stop api worker scheduler neo4j >/dev/null 2>&1 || true
    say "已停 api/worker/scheduler/neo4j，开始 build"
    docker build -t "${IMAGE}:next" ./backend || die "构建失败，栈保持停止，执行 guard.sh restart-api 前请先排查"
    docker image inspect "$IMAGE:latest" >/dev/null 2>&1 && docker tag "$IMAGE:latest" "$prev" && say "已保留回滚镜像 $prev"
    docker tag "${IMAGE}:next" "${IMAGE}:latest"
    docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --no-build || die "拉起失败，回滚镜像在 $prev"
  )
  if ! wait_ready 40; then
    warn "新镜像未通过健康检查，自动回滚到 $prev"
    docker tag "$prev" "${IMAGE}:latest"
    ( cd "$ROOT" && docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --no-build )
    wait_ready 30 && die "已回滚并从旧镜像恢复；新代码有问题，别上线"
    die "回滚后仍未恢复，立刻人工介入"
  fi
  say "重建完成：ready=200"
}

cmd_deploy_dist() {
  need_root; need_yes
  local tar="${1:-}"
  [ -n "$tar" ] || die "用法：deploy-dist <tar.gz 路径> --yes"
  [ -f "$tar" ] || die "找不到 $tar"
  local size baksize
  size=$(stat -c %s "$tar")
  [ "$size" -gt 100000 ] || die "包太小（${size}B），疑似上传失败"

  local tmp; tmp=$(mktemp -d /tmp/dist-new.XXXXXX)
  tar -xzf "$tar" -C "$tmp" || die "解包失败"
  local src="$tmp/dist"
  [ -f "$src/index.html" ] || die "解包后没有 dist/index.html，包结构不对"

  local bak="/root/dist-backup-$(date +%Y%m%d-%H%M%S)"
  cp -a "$DIST_DIR" "$bak" 2>/dev/null && say "旧产物备份 $bak"
  rm -rf "${DIST_DIR}.next"
  cp -a "$src" "${DIST_DIR}.next" || die "复制新产物失败"
  chmod -R 755 "${DIST_DIR}.next"
  mv "$DIST_DIR" "${DIST_DIR}.old" && mv "${DIST_DIR}.next" "$DIST_DIR" && rm -rf "${DIST_DIR}.old"
  say "已替换 dist（$(du -sh "$DIST_DIR" | cut -f1)）"

  nginx -t || { rm -rf "$DIST_DIR"; [ -d "$bak" ] && cp -a "$bak" "$DIST_DIR"; die "nginx 配置校验失败，已回滚"; }
  systemctl reload nginx

  sleep 1
  local home; home=$(http_code "$BASE/")
  [ "$home" = "200" ] || { [ -d "$bak" ] && rm -rf "$DIST_DIR" && cp -a "$bak" "$DIST_DIR" && systemctl reload nginx; die "首页 ${home}，已回滚到 $bak"; }
  say "部署完成：home=200，回滚点 $bak"
}

cmd_backup() {
  need_root; need_yes
  [ -x /opt/backup/backup.sh ] || die "缺少 /opt/backup/backup.sh"
  /opt/backup/backup.sh
  ls -lh /opt/backup | tail -3
}

cmd_verify() {
  local ok=0 bad=0
  chk() { # name expect actual
    if [ "$2" = "$3" ]; then printf 'PASS  %-22s %s\n' "$1" "$3"; ok=$((ok+1))
    else printf 'FAIL  %-22s got=%s want=%s\n' "$1" "$3" "$2"; bad=$((bad+1)); fi
  }
  chk "health/ready" "200" "$(http_code "$BASE/health/ready")"
  chk "health/live"  "200" "$(http_code "$BASE/health/live")"
  chk "home"         "200" "$(http_code "$BASE/")"
  chk "guest"        "201" "$(curl -s -o /dev/null -w '%{http_code}' -X POST --max-time 10 "$BASE/api/v1/auth/guest")"
  chk "register-off" "403" "$(curl -s -o /dev/null -w '%{http_code}' -X POST --max-time 10 -H 'Content-Type: application/json' -d '{"username":"probe_x","password":"probe_pass_1","display_name":"p","invite_code":"x"}' "$BASE/api/v1/auth/register")"
  local up; up=$(docker ps --format '{{.Names}}' | grep -c '^job-graph-')
  chk "containers-up" "6" "$up"
  local noLimit; noLimit=$(docker ps --format '{{.Names}}' | grep '^job-graph-' | while read -r c; do docker inspect -f '{{.HostConfig.Memory}}' "$c"; done | grep -c '^0$')
  chk "mem-limits-set" "0" "$noLimit"
  printf '\nresult: pass=%s fail=%s\n' "$ok" "$bad"
  [ "$bad" -eq 0 ] || return 1
}

cmd_rollback() {
  echo "== 镜像回滚点 =="; docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}' | grep '^job-graph-backend:' || true
  echo "== 前端回滚点 =="; ls -dt /root/dist-backup-* 2>/dev/null | head -5 || echo none
  echo "== 源码回滚点 =="; ls -dt /root/backend-src-backup-* 2>/dev/null | head -5 || echo none
  echo "== env 回滚点 ==";  ls -t /root/env.bak.* 2>/dev/null | head -5 || echo none
  cat <<'EOS'

回滚示例（人工执行，先看清再动）：
  镜像：  docker tag job-graph-backend:prev-<ts> job-graph-backend:latest \
          && cd /opt/job-graph && docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --no-build
  前端：  rm -rf /var/www/job-graph/dist \
          && cp -a /root/dist-backup-<ts> /var/www/job-graph/dist && systemctl reload nginx
EOS
}

usage() {
  cat <<'EOS'
guard.sh — 生产服务器唯一变更入口

只读：
  status | health | logs <svc> [lines] | rollback

变更（必须加 --yes）：
  restart-api --yes
  safe-rebuild --yes              内存<1500M 自动拒绝；失败自动回滚
  deploy-dist <tar.gz> --yes      替换 dist 并校验首页
  backup --yes
  verify                          验收（只读）

禁止事项见 docs/服务器Agent操作手册.md
EOS
}

main() {
  local cmd="${1:-help}"; shift || true
  CONFIRM=0
  local args=()
  for a in "$@"; do
    case "$a" in
      --yes|-y) CONFIRM=1 ;;
      *) args+=("$a") ;;
    esac
  done
  set -- "${args[@]+"${args[@]}"}"

  case "$cmd" in
    status)       cmd_status ;;
    health)       cmd_health ;;
    logs)         cmd_logs "${1:-api}" "${2:-200}" ;;
    restart-api)  cmd_restart_api ;;
    safe-rebuild) cmd_safe_rebuild ;;
    deploy-dist)  cmd_deploy_dist "${1:-}" ;;
    backup)       cmd_backup ;;
    verify)       cmd_verify ;;
    rollback)     cmd_rollback ;;
    help|-h|--help) usage ;;
    *) usage; die "未知命令：$cmd" ;;
  esac
}

main "$@"
