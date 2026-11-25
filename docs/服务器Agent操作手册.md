# 服务器 Agent 操作手册（组内通用）

> 版本：2026-11-24　维护人：项目负责人　适用对象：**组员的 AI Agent**
> 配套文件：根目录 `AGENTS.md`（约束摘要）、`deploy/guard.sh`（唯一变更入口）
> 本文所有数字均为**实测值**，不是估算。

---

## 0. 给 agent 的三句话

1. 这台机器是**生产环境**，上面有真实的学生简历（个人信息），不是沙箱。
2. 你**只能**通过 `bash /opt/job-graph/deploy/guard.sh <命令>` 做变更；其余命令只允许只读。
3. 说"完成"必须贴命令输出；没有输出 = 没做。

---

## 1. 服务器事实卡

| 项 | 值 |
| --- | --- |
| 公网入口 | `http://8.153.183.119:8080`（nginx → 前端静态 + `/api/` 反代） |
| 实例 ID | `i-uf6iflhtvqs3pp833eya`（阿里云 ECS，华东2 上海） |
| 规格 | 2 vCPU / 3.5 GiB / ESSD Entry 60 GiB / Ubuntu 22.04.5 |
| 内存现状 | 容器常驻 ≈1.6 GB，**余量 ≈1.7 GB**（唯一稀缺资源） |
| 容器 | `job-graph-api-1`、`job-graph-worker-1`、`job-graph-scheduler-1`、`job-graph-postgres-1`、`job-graph-redis-1`、`job-graph-neo4j-1`（`migrate` 是一次性任务，`exited 0` 属正常） |
| 内存上限 | api 700M / worker 1GiB / scheduler 200M / postgres 600M / redis 200M / neo4j 1GiB |
| 端口 | `8080` nginx（公网）；`8000` api、`5432` postgres、`7474/7687` neo4j 均只绑 `127.0.0.1` |
| 代码目录 | `/opt/job-graph`（`compose.yaml`、`deploy/`、`backend/`、`frontend/`） |
| 前端产物 | `/var/www/job-graph/dist`（nginx root） |
| 密钥文件 | `/opt/job-graph/.env`（权限 600，**内容禁止外泄**） |
| 备份 | `/opt/backup`（每日 03:20 cron，保留 7 天） |
| 巡检 | `deploy/monitor.sh`（每 5 分钟 cron，日志 `/var/log/job-graph-monitor.log`） |
| 开机预热 | `job-graph-warmup.service`（oneshot，重启后自动打热图谱缓存） |

**运维通道**：阿里云 ECS Workbench（带外）+ RAM 子账号 AccessKey，命令形如

```bash
workbench exec --instance-id i-uf6iflhtvqs3pp833eya --command "<命令>" --timeout 60
workbench upload ./x.tar.gz /root/x.tar.gz --instance-id i-uf6iflhtvqs3pp833eya --force
```

> 注意：`workbench` 的命令串里**不要带短横线参数**（如 `-a`、`-v`），会被它自己解析掉。
> 多步骤一律写成 `.sh` 上传后 `bash` 执行。

---

## 2. 红线（违反即事故）

| # | 禁止 | 真实后果 |
| --- | --- | --- |
| P0-1 | 容器全运行状态下 `docker compose up -d --build` | 2C4G 内存打满 → **整机 hang**，内核连 OOM 日志都不留，只能控制台重启（2026-11-23 00:10 确实发生） |
| P0-2 | 用未校验的文件 `docker cp` 覆盖容器内代码 | 643 行旧文件覆盖 1206 行 → api `ImportError` → 全站 **502 约 4 分钟**（2026-11-24 10:58） |
| P0-3 | `.env` / API Key / root 密码 / Cookie token 入仓库或进日志 | 凭据泄露，学生数据可被读取 |
| P0-4 | 改 nginx 的 `listen 8080`、`/api/` 反代、`client_max_body_size 20m` | 学生上传简历直接失败 |
| P0-5 | 删 `/opt/backup`、`/root/*-backup-*`、`prev-*` 镜像 | 失去唯一回滚路径 |
| P0-6 | 在服务器跑 `npm run build` / `uv sync` 构建 | 同 P0-1，内存峰值 |

**为什么这么严**：这台机器的容器都有内存上限，但构建进程没有；历史上两次宕机都是"构建"或"覆盖文件"引起的，不是业务代码本身的 bug。

---

## 3. 权限与准入

| 角色 | 能做什么 | 怎么拿 |
| --- | --- | --- |
| 读仓库 | 看代码、改本地分支 | 无门槛 |
| 只读服务器 | `guard.sh status/health/logs` | RAM 子账号 + `ecs:DescribeInstances` 等只读权限 |
| 变更服务器 | `guard.sh restart-api/safe-rebuild/deploy-dist` | 在只读基础上加 `ecs-workbench:LoginECSInstance`、`ecs:StartTerminalSession` |

申请流程（找负责人）：

1. 说明要做什么（一句话）和预计改哪些文件；
2. 负责人建 RAM 子账号，勾选「OpenAPI 调用访问」，保存一次性 AK；
3. 把 AK 写进**本机** `~/.workbench/config.json`（600 权限），**不要发群里、不要进仓库**；
4. 对齐验收口径（见 §5）。

---

## 4. 三十秒上手

```bash
# 1) 先看现场（只读，随便跑）
bash /opt/job-graph/deploy/guard.sh status

# 2) 看某个服务日志
bash /opt/job-graph/deploy/guard.sh logs api 200

# 3) 验收（只读，交付前必跑）
bash /opt/job-graph/deploy/guard.sh verify
```

`status` 会打印：时间、内存、磁盘、容器状态、**每个容器的内存上限**、四个 HTTP 码（live/ready/home/guest）、当前 cron。

---

## 5. 四类任务的标准动作

### 5.1 改前端（最常见）

```bash
# 本地：构建（务必带生产基址）
$env:VITE_API_BASE_URL='http://8.153.183.119:8080'
npm --prefix frontend run build

# 本地：自检产物（PowerShell 控制台中文不可信，用 python 读）
python -c "import pathlib,sys;h=pathlib.Path('frontend/dist/index.html').read_text('utf-8');print('title_ok', '\u5c97\u667a\u878d\u6790' in h);print('viewport_ok','viewport-fit=cover' in h)"
grep -rl "127.0.0.1:8000" frontend/dist || echo "no_localhost_ok"

# 本地：打包上传
tar -czf dist.tar.gz -C frontend dist
workbench upload ./dist.tar.gz /root/dist.tar.gz --instance-id i-uf6iflhtvqs3pp833eya --force

# 服务器：部署（自动备份 + nginx 校验 + 首页探测 + 失败回滚）
bash /opt/job-graph/deploy/guard.sh deploy-dist /root/dist.tar.gz --yes
```

### 5.2 改后端

```bash
# 本地：改代码 → lint/测试能跑就跑 → 打包（排除虚拟环境与缓存）
tar -czf backend.tar.gz --exclude='backend/.venv' --exclude='*/__pycache__' backend
workbench upload ./backend.tar.gz /root/backend.tar.gz --instance-id i-uf6iflhtvqs3pp833eya --force

# 服务器：解包 → 重建（内建内存门槛 1500M、prev 镜像、失败自动回滚）
cd /opt/job-graph && tar -xzf /root/backend.tar.gz -C / --strip-components=1 2>/dev/null || tar -xzf /root/backend.tar.gz
bash /opt/job-graph/deploy/guard.sh safe-rebuild --yes
bash /opt/job-graph/deploy/guard.sh verify
```

> 解包前先 `cp -a /opt/job-graph/backend /root/backend-src-backup-$(date +%Y%m%d-%H%M%S)`，guard.sh 不管源码备份。

### 5.3 改配置

```bash
# 只改 .env（600），改前备份
cp -a /opt/job-graph/.env /root/env.bak.$(date +%Y%m%d-%H%M%S)
# 编辑后
bash /opt/job-graph/deploy/guard.sh restart-api --yes
bash /opt/job-graph/deploy/guard.sh verify
```

> 容器不会自动重读 `.env`：`restart` 无效，必须 `up -d --no-build --no-deps api`（guard.sh 的 restart-api 已封装）。

### 5.4 查故障

```bash
bash /opt/job-graph/deploy/guard.sh status
bash /opt/job-graph/deploy/guard.sh logs api 200
bash /opt/job-graph/deploy/guard.sh logs worker 200
tail -50 /var/log/nginx/access.log
tail -20 /var/log/job-graph-monitor.log
```

---

## 6. 故障决策树

```
网站打不开
├─ ready=200 且 home=200 ？ → 服务正常，问题在客户端网络（换网络/开热点再试）
├─ ready=503 ？
│   ├─ 看 dependencies 里哪个 down
│   │   ├─ neo4j down  → guard.sh logs neo4j → 常见是内存不足被杀 → guard.sh status 看余量
│   │   ├─ postgres down → logs postgres
│   │   └─ redis down   → logs redis
│   └─ 全部 down → 大概率整机 h ang → 控制台重启
└─ ready 超时（000）？
    ├─ 先在服务器本地 curl 127.0.0.1:8080/health/live
    │   ├─ 本地 200 → 公网链路/安全组问题
    │   └─ 本地也 000 → api 死了 → guard.sh logs api → guard.sh restart-api --yes
    └─ 内存 < 200M → 先停 worker/scheduler 释放，再重启 api
```

**禁止**：`docker system prune`、`docker restart $(docker ps -q)`、`reboot`。这三个都会造成不可控后果。

---

## 7. 回滚手册

```bash
bash /opt/job-graph/deploy/guard.sh rollback   # 列出所有回滚点
```

| 类型 | 位置 | 用法 |
| --- | --- | --- |
| 镜像 | `job-graph-backend:prev-<ts>` | `docker tag ...:latest` 后 `up -d --no-build` |
| 前端 | `/root/dist-backup-<ts>` | 覆盖 `/var/www/job-graph/dist` 后 `systemctl reload nginx` |
| 源码 | `/root/backend-src-backup-<ts>` | 覆盖 `/opt/job-graph/backend` 后 `safe-rebuild` |
| 配置 | `/root/env.bak.<ts>` | 覆盖 `.env` 后 `restart-api` |
| 数据库 | `/opt/backup/<date>/postgres.sql.gz` | `gunzip -c ... \| docker exec -i job-graph-postgres-1 psql -U job_graph -d job_graph` |

---

## 8. 给组员 agent 的提示词模板（直接粘）

```text
你是「岗智融析」项目的运维 Agent。开工前先读两处：
1) 仓库根目录 AGENTS.md
2) docs/服务器Agent操作手册.md

硬约束：
- 生产服务器 8.153.183.119，学生真实数据，不得做破坏性操作。
- 所有变更只能通过 bash /opt/job-graph/deploy/guard.sh <cmd> --yes 执行；
  只读诊断用 status / health / logs / verify。
- 禁止：容器全运行时 docker compose up -d --build；在服务器 npm build；
  docker cp 覆盖容器内代码；改动 nginx 的 8080 与 /api/ 反代；输出任何密钥或 token。
- 前端改动必须本地构建、本地自检产物、上传后用 guard.sh deploy-dist 部署。

交付格式（缺一不可）：
1) 改了什么（文件 + 行为）
2) 证据（guard.sh verify 或 curl 的真实输出）
3) 回滚点（路径或镜像标签）
4) 剩余风险
```

---

## 9. 两次真实事故（读懂红线）

**① 2026-11-23 00:10 整机 hang**
首次部署时在容器全运行状态下构建镜像，服务器到 pypi 只有 18.8 KB/s，构建长时间挂住并持续吃内存；`dmesg`/`journalctl` 里**没有任何 OOM kill 记录** —— 不是被内核杀，是整机卡死。最终只能从阿里云控制台重启。
→ 所以：构建必须先停栈、先看内存余量（guard.sh 已内置 1500M 门槛）。

**② 2026-11-24 10:58 全站 502**
同步 `resumes/service.py` 时 `workbench upload` 因 OSS DNS 超时失败，脚本没中止，继续用 `/tmp` 里残留的 643 行旧文件覆盖容器内 1206 行文件 → `ImportError: cannot import name 'get_visible_resume'` → api 退出。恢复方式是删容器 + 从镜像重建，中断约 4 分钟。
→ 所以：上传必须校验字节数/行数，覆盖前先比对；`guard.sh deploy-dist` 会校验包大小和结构。

---

## 10. 升级路径

| 场景 | 找谁 |
| --- | --- |
| 不知道该不该改 | 先停手，把 `guard.sh status` 贴给项目负责人 |
| 需要新权限 | 项目负责人（建 RAM 子账号） |
| 站点整体不可用且重启无效 | 阿里云控制台 → 实例 → 重启（这是唯一保留的"大招"） |
| 数据丢失/需要恢复 | 项目负责人 + `/opt/backup` 最近一份 |

---

## 11. 网络环境约束（重要：**不需要访问国外网络**）

组内常见误解是"要用这台服务器得能翻墙"。**不需要。** 下面逐条列出各环节的真实出网要求（均为实测）。

| 环节 | 实际依赖 | 国内直连？ |
| --- | --- | --- |
| 阿里云控制台 / 网页版 Workbench | `*.console.aliyun.com`、`ecs-workbench.aliyuncs.com` | ✅ 可以 |
| workbench CLI 下载 | `workbench-cli.oss-cn-hangzhou.aliyuncs.com` | ✅ 可以（实测 200） |
| workbench CLI 执行命令 | `ecs-workbench.aliyuncs.com` | ✅ 可以 |
| 服务器调 LLM | `api.deepseek.com` | ✅ 可以（服务器侧实测 401 可达） |
| npm 装包（如需） | `registry.npmmirror.com` | ✅ 可以（已设镜像） |
| Docker 拉镜像 | `docker.m.daocloud.io` 镜像 | ✅ 已配（**Docker Hub 直连被墙，实测 000**） |
| PyPI 装包 | `mirrors.aliyun.com/pypi` | ✅ 已配（官方源实测 18.8 KB/s，慢到会挂） |
| ❌ opencode.ai / Cloudflare 类服务 | 国外 | ❌ 不可用，别把 LLM 换成这类 |

**结论**：只要走 `guard.sh` + 阿里云控制台，组员在**纯国内网络**下可以完整操作这台服务器。

### 11.1 三种接入方式（按门槛从低到高）

| 方式 | 需要什么 | 适合谁 | 限制 |
| --- | --- | --- | --- |
| **① 控制台网页 Workbench** | 一个阿里云账号（被授权） | 所有人，零安装 | 复制粘贴命令，无文件上传之外的自动化 |
| **② workbench CLI** | RAM 子账号 AK | 要写脚本/自动化的人 | 需要 `npm`/exe 安装（下载走国内 OSS） |
| **③ 只看状态** | 浏览器打开站点 | 只想知道"还活着吗" | 只能看到站点本身，看不到服务器指标 |

**方式①的具体路径**（国内网络可用）：
阿里云控制台 → ECS → 实例 `i-uf6iflhtvqs3pp833eya` → 远程连接 → Workbench 远程连接 → 登录 `root` → 粘贴：

```bash
bash /opt/job-graph/deploy/guard.sh status
```

**方式②的关键配置**（一次性，全部国内源）：

```bash
npm config set registry https://registry.npmmirror.com
# 安装 workbench CLI（走国内 OSS，无需外网）
irm https://workbench-cli.oss-cn-hangzhou.aliyuncs.com/install.ps1 | iex
# 配置 RAM 子账号（AK 只写本机，600 权限）
workbench config
```

### 11.3 开着 TUN / 全局代理就连不上服务器？（高频问题）

**症状**（实测复现过）：TCP 端口是通的（`Test-NetConnection` 返回 True），但

- SSH 停在 `Connection timed out during banner exchange` 或 `No existing session`；
- 网站 `curl` 一直 `000`，浏览器转圈后报 `ERR_EMPTY_RESPONSE`；
- 关掉代理立刻恢复正常。

**两个原因，通常同时存在**：

1. **全局代理把国内流量绕出国**：访问 `8.153.183.119` 的包被送到美国出口，再回国内，链路长且中间设备可能丢包；
2. **TUN 的 MTU 太大**：TUN 默认 1500（有的客户端 9000），叠加隧道封装后实际可用约 1400 以下。TCP 握手是小包能过，SSH 的 KEXINIT（约 1 KB）、HTTP 响应头（200 B~2 KB）是大包，直接被丢 → 表现就是"端口通、但拿不到任何数据"。

**处理（改客户端，不改服务器）**：

① 加直连规则（首选，一次配好永久有效）：

```yaml
# Clash / Clash Verge / Clash Meta（规则模式，不要全局模式）
rules:
  - IP-CIDR,8.153.183.119/32,DIRECT,no-resolve
  - DOMAIN-SUFFIX,aliyuncs.com,DIRECT
  - DOMAIN-SUFFIX,aliyun.com,DIRECT
```

- v2rayN：设置 → 路由设置 → 直连 IP 加入 `8.153.183.119`
- Shadowrocket：配置 → 跳过代理 → 添加 `8.153.183.119`、`*.aliyuncs.com`
- sing-box：`{"ip_cidr":["8.153.183.119/32"],"outbound":"direct"}`

② 把 TUN 的 MTU 改成 **1280**（Clash Verge：设置 → TUN → MTU；v2rayN/v2rayNG 同理）。

③ 验证（两条都过才算好）：

```bash
curl -m 10 -o /dev/null -w "%{http_code}\n" http://8.153.183.119:8080/health/live   # 期望 200
ssh -o ConnectTimeout=10 root@8.153.183.119                                        # 期望出现密码提示
```

④ 兜底：阿里云控制台 → Workbench 远程连接（浏览器会话，走阿里云内网通道，同样建议先把 `*.aliyuncs.com` 加直连）。

### 11.2 如果组员的 AI agent 本身需要国外模型

那是**agent 的问题，不是服务器的问题**。三条路，任选：

1. 让组员把 `guard.sh status` 的输出**贴给任意国内可用的 AI**（DeepSeek 网页版、通义、Kimi），由它判断；
2. agent 走国内模型接口（DeepSeek 官方 API 国内直连）；
3. 需要 agent 直接连服务器时，用方式①的网页 Workbench —— agent 生成命令，人粘贴执行，**这是最省事也最安全的组合**。

---

## 附录 A：一条命令看全景

```bash
bash /opt/job-graph/deploy/guard.sh status
```

输出示例（2026-11-24 实测）：内存 used≈1.6G / available≈1.7G，6 容器 Up，`live=200 ready=200 home=200 guest=201`。
