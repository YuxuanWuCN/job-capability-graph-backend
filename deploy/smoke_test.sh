#!/usr/bin/env bash
# 岗智融析 上线冒烟测试
# 用法: bash smoke_test.sh http://IP:8080 admin '你的密码'
set -uo pipefail
BASE=${1:-http://127.0.0.1:8080}
USER=${2:-admin}
PASS=${3:-}
JAR=$(mktemp); pass=0; fail=0
ok(){ echo "  [OK] $1"; pass=$((pass+1)); }
no(){ echo "  [FAIL] $1  ($2)"; fail=$((fail+1)); }
chk(){ if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "got=$2 want=$3"; fi; }

echo "== 基础连通 =="
chk "首页 200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" 200
chk "live 200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health/live")" 200
chk "ready 200" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health/ready")" 200

echo "== 登录与会话 =="
code=$(curl -s -c "$JAR" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}")
chk "登录 200" "$code" 200
chk "me 200" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/v1/auth/me")" 200

echo "== 目录与图谱（学生推荐的前置数据）=="
chk "目录能力 200" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/v1/catalog/capabilities")" 200
chk "目录岗位 200" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/v1/catalog/job-roles")" 200
chk "已发布图谱 200" "$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/v1/graph")" 200

echo "== 管理员依赖面板 =="
dep=$(curl -s -b "$JAR" "$BASE/api/v1/admin/system/dependencies")
echo "  ${dep:0:300}"

echo "== 注册开关 =="
reg=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/register" \
  -H 'Content-Type: application/json' -d '{"username":"smoke_probe_user","password":"probe-not-valid","display_name":"x","invite_code":"wrong"}')
echo "  [INFO] 注册探测返回 $reg （400/403 = 已开启且校验生效；404 = 未实现）"

echo "---- 结果: 通过 $pass / 失败 $fail ----"
rm -f "$JAR"
[ "$fail" -eq 0 ]
