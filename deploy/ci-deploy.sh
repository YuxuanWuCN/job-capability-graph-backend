#!/usr/bin/env bash
# ci-deploy.sh — CI 部署编排（薄封装，所有变更仍然只经 guard.sh）
#
# 用法（服务器上，root）：
#   bash /root/ci-deploy.sh both       # 先后端再前端
#   bash /root/ci-deploy.sh backend
#   bash /root/ci-deploy.sh frontend
#
# 前置：/root/backend.tar.gz 与/或 /root/dist.tar.gz 已上传
#
# 设计约束（见 AGENTS.md / docs/服务器Agent操作手册.md）：
#   - 不在服务器构建前端（P0-6），前端产物必须由 CI 构建后上传
#   - 后端重建只走 guard.sh safe-rebuild（内建 1500M 内存门槛 + prev 镜像 + 失败回滚）
#   - 解包前备份源码（guard.sh 不管源码备份）
#   - 任一环节失败立即退出，不继续往下做

set -euo pipefail

ROOT="${JOB_GRAPH_ROOT:-/opt/job-graph}"
GUARD="$ROOT/deploy/guard.sh"
TARGET="${1:-both}"
STAMP="$(date +%Y%m%d-%H%M%S)"

say() { printf '[ci-deploy] %s\n' "$*"; }
die() { printf '[ci-deploy][ABORT] %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root"
[ -f "$GUARD" ] || die "找不到 $GUARD"
case "$TARGET" in
  both|backend|frontend) ;;
  *) die "未知目标：$TARGET（可选 both/backend/frontend）" ;;
esac

say "目标=$TARGET  时间=$STAMP"
say "部署前现场："
free -m | head -2
bash "$GUARD" status | sed -n '/===== 健康 =====/,+1p'

# ---------- 后端 ----------
if [ "$TARGET" = "both" ] || [ "$TARGET" = "backend" ]; then
  TAR=/root/backend.tar.gz
  [ -f "$TAR" ] || die "缺少 $TAR"
  SIZE=$(stat -c %s "$TAR")
  [ "$SIZE" -gt 100000 ] || die "后端包过小（${SIZE}B），疑似上传失败"

  # 包结构自检：必须以 backend/ 开头，避免解包污染 /opt/job-graph 或 /
  tar -tzf "$TAR" | head -1 | grep -q '^backend/' \
    || die "包结构不对：首条目不是 backend/，拒绝解包"

  BAK="/root/backend-src-backup-$STAMP"
  cp -a "$ROOT/backend" "$BAK"
  say "源码已备份 $BAK"

  # 正确解包形式：包内自带 backend/ 前缀，直接解到 $ROOT
  # 注意：手册 §5.2 里 "-C / --strip-components=1" 的写法会把文件散到根目录，此处不使用
  tar -xzf "$TAR" -C "$ROOT"
  say "源码已更新"

  bash "$GUARD" safe-rebuild --yes
  bash "$GUARD" verify
  say "后端完成"
fi

# ---------- 前端 ----------
if [ "$TARGET" = "both" ] || [ "$TARGET" = "frontend" ]; then
  TAR=/root/dist.tar.gz
  [ -f "$TAR" ] || die "缺少 $TAR"
  bash "$GUARD" deploy-dist "$TAR" --yes
  say "前端完成"
fi

say "最终验收："
bash "$GUARD" verify
say "全部完成。回滚点：镜像 prev-* / 前端 /root/dist-backup-* / 源码 /root/backend-src-backup-$STAMP"
