#!/usr/bin/env bash
# 岗智融析 · 轻量看门狗（无外部依赖，cron 每 5 分钟一次）
# 作用：把「是否健康」变成可回溯的日志；站点真挂了自动拉一次 api；内存见底/队列堆积时留痕。
# 安装：cp 到 /opt/job-graph/deploy/monitor.sh，chmod +x，crontab 加 */5 * * * *
set -u

LOG=/var/log/job-graph-monitor.log
BASE=http://127.0.0.1:8080
TS=$(date '+%F %T')

ready=$(curl -s -m 6 -o /dev/null -w '%{http_code}' "$BASE/health/ready" || echo 000)
home=$(curl -s -m 6 -o /dev/null -w '%{http_code}' "$BASE/" || echo 000)
avail=$(free -m | awk '/^Mem:/{print $7}')
disk=$(df -m / | awk 'NR==2{print $5}' | tr -d '%')
queue=$(docker exec job-graph-redis-1 redis-cli llen celery 2>/dev/null)
queue=${queue:-NA}
swapused=$(free -m | awk '/^Swap:/{print $3}')

echo "$TS ready=$ready home=$home avail=${avail}M disk=${disk}% queue=$queue swap=${swapused}M" >> "$LOG"

# 1) 站点完全不可用 -> 拉一次 api（不会重建容器，10 秒级恢复）
if [ "$ready" != "200" ] && [ "$home" != "200" ]; then
    echo "$TS ALERT 站点不可用，重启 api" >> "$LOG"
    docker restart job-graph-api-1 >> "$LOG" 2>&1
    sleep 10
    after=$(curl -s -m 6 -o /dev/null -w '%{http_code}' "$BASE/health/ready" || echo 000)
    echo "$TS RECOVER api 重启后 ready=$after" >> "$LOG"
fi

# 2) 内存见底（cgroup 限额下低于 200M 可用内存，解析会 OOM）
if [ "${avail:-9999}" -lt 200 ] 2>/dev/null; then
    echo "$TS ALERT 可用内存仅 ${avail}M" >> "$LOG"
fi

# 3) 磁盘超过 85%（简历文件 + 日志 + 镜像）
if [ "${disk:-0}" -gt 85 ] 2>/dev/null; then
    echo "$TS ALERT 磁盘使用 ${disk}%" >> "$LOG"
fi

# 4) 解析队列堆积（学生集中上传时预警，>50 意味着等待超过 5 分钟）
if [ "$queue" != "NA" ] && [ "$queue" -gt 50 ] 2>/dev/null; then
    echo "$TS ALERT 解析队列积压 depth=$queue" >> "$LOG"
fi

# 5) 日志自限额，避免一个月把盘写满
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 5000000 ]; then
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
