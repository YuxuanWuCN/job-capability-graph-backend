#!/usr/bin/env bash
# 阿里云 ECS（Ubuntu 22.04，2核4G）一键初始化
# 用法：sudo bash deploy/server_setup.sh
#
# 做四件事：装 Docker → 配镜像加速 → 加 2G Swap → 建目录
# 可重复执行，已完成的步骤会自动跳过。

set -euo pipefail

echo "===== 1/4 安装基础工具 ====="
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates gnupg

echo "===== 2/4 安装 Docker ====="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
else
  echo "Docker 已安装，跳过"
fi
systemctl enable --now docker
docker --version
docker compose version

echo "===== 3/4 配置镜像加速 + 2G Swap ====="
# 国内直连 Docker Hub 经常超时；换加速源。如果你有阿里云「容器镜像服务」的专属加速地址，
# 把下面 daocloud 那行替换成 https://<你的编码>.mirror.aliyuncs.com 更快更稳。
if [ ! -f /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
JSON
  systemctl restart docker
  echo "已写入 /etc/docker/daemon.json"
else
  echo "/etc/docker/daemon.json 已存在，跳过"
fi

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "已添加 2G swap"
else
  swapon /swapfile 2>/dev/null || true
  echo "swap 已存在"
fi

sysctl -w vm.swappiness=60
echo 'vm.swappiness=60' > /etc/sysctl.d/99-swap.conf

echo "===== 4/4 建目录 ====="
mkdir -p /opt/job-graph /var/www/job-graph

echo
echo "===== 完成，当前状态 ====="
free -h
df -h / | tail -2
docker --version
echo
echo "下一步："
echo "  1) 把 deploy-bundle.tar.gz 传到 /root/"
echo "  2) tar -xzf /root/deploy-bundle.tar.gz -C /opt/job-graph"
echo "  3) cd /opt/job-graph && cp deploy/.env.production .env && vi .env"
echo "  4) 严禁在服务运行时直接执行 up -d --build（2C4G 会内存打满假死），按以下任一安全姿势启动："
echo "     姿势 A（推荐）：本地 build -> docker save job-graph-backend:latest | gzip > image.tgz -> 上传后 docker load -> docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --no-build"
echo "     姿势 B（本机现场 build）：先确认 free -h available >= 1.5Gi（如已运行则先 docker compose -f compose.yaml -f deploy/compose.prod.yaml stop worker scheduler neo4j api），再 build api，最后 up -d --no-build"
