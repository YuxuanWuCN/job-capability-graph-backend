#!/usr/bin/env bash
# 接口预热脚本
#
# 为什么需要：容器一重启，Neo4j 的页缓存和 JVM 都是空的，第一次访问
#   /api/v1/graph 要 5~7 秒（实测 6.9s），之后才回到 0.2 秒。
# 学生第一次打开图谱页正好撞上这个冷启动，看起来就是"网站卡住"。
#
# 这个脚本在启动后主动把热点接口打一遍，把冷启动代价挪到没人用的时刻。
#
# 用法：
#   bash deploy/warmup.sh                  # 手动预热
#   systemctl start job-graph-warmup       # 由 systemd 触发（开机自动跑一次）
#
# 环境变量（可选，写在 /opt/job-graph/deploy/warmup.env，权限 600）：
#   BASE            默认 http://127.0.0.1:8080
#   WARMUP_USER     登录用户名；留空则用游客会话
#   WARMUP_PASS     登录密码
#   READY_TIMEOUT   等待服务就绪的秒数，默认 180

set -u

BASE="${BASE:-http://127.0.0.1:8080}"
READY_TIMEOUT="${READY_TIMEOUT:-180}"
JAR="$(mktemp)"

# 配置文件存在则加载（放密码用，不要写进这个脚本）
if [ -f /opt/job-graph/deploy/warmup.env ]; then
  # shellcheck disable=SC1091
  . /opt/job-graph/deploy/warmup.env
fi

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
cleanup() { rm -f "$JAR"; }
trap cleanup EXIT

# --- 1. 等服务就绪 ---------------------------------------------------------
log "等待 /health/ready（最多 ${READY_TIMEOUT}s）"
deadline=$(( $(date +%s) + READY_TIMEOUT ))
while :; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE/health/ready" || echo 000)"
  if [ "$code" = "200" ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    log "超时：/health/ready 返回 ${code}，放弃预热"
    exit 1
  fi
  sleep 3
done
log "服务已就绪"

# --- 2. 建立会话 -----------------------------------------------------------
if [ -n "${WARMUP_USER:-}" ]; then
  code="$(curl -s -o /dev/null -c "$JAR" -w '%{http_code}' --max-time 15 \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${WARMUP_USER}\",\"password\":\"${WARMUP_PASS:-}\"}" \
    "$BASE/api/v1/auth/login" || echo 000)"
  log "auth/login(${WARMUP_USER}) -> ${code}"
else
  code="$(curl -s -o /dev/null -c "$JAR" -w '%{http_code}' --max-time 15 \
    -X POST "$BASE/api/v1/auth/guest" || echo 000)"
  log "auth/guest -> ${code}"
fi

# --- 3. 打热点接口 ---------------------------------------------------------
warm() {
  local path="$1" label="$2"
  local stats
  stats="$(curl -s -o /dev/null -b "$JAR" -H 'Accept-Encoding: gzip' \
    -w '%{http_code} ttfb=%{time_starttransfer}s bytes=%{size_download}' \
    --max-time 120 "$BASE$path" || echo 'curl-failed')"
  log "${label} -> ${stats}"
}

warm "/"                                                          "首页"
warm "/api/v1/catalog/job-roles?include_candidates=false"          "岗位列表"
warm "/api/v1/graph"                                              "图谱第 1 次（冷）"
warm "/api/v1/graph"                                              "图谱第 2 次（热）"
warm "/api/v1/graph/emerging-jobs?page=1&page_size=100"            "新兴岗位"

log "预热完成"
