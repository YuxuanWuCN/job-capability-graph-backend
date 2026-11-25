# AGENTS.md · 本仓库的 Agent 行为约束

> 适用范围：所有在本仓库工作的 AI Agent（含组员的 agent、外部协作 agent）。
> 详细版见 `docs/服务器Agent操作手册.md`。**本文件与手册冲突时，以手册为准。**

---

## 0. 三句话版本

1. 这是**生产机**（`8.153.183.119:8080`），不是开发机；任何写操作都要先想回滚。
2. 服务器上**只允许通过 `deploy/guard.sh` 做变更**，禁止自创命令。
3. 声称"完成"之前，必须贴出验收命令的**真实输出**。

---

## 1. 绝对禁止（违反即视为事故）

| # | 禁止行为 | 后果（已发生过） |
| --- | --- | --- |
| P0-1 | 在容器全运行时执行 `docker compose up -d --build` | 2C4G 内存打满，**整机 hang**，只能控制台重启（11-23 00:10） |
| P0-2 | 用未校验的文件覆盖容器内代码（`docker cp`） | api `ImportError` → 全站 502 约 4 分钟（11-24 10:58） |
| P0-3 | 把 `.env`、API key、root 密码、Cookie token 写进仓库或输出到日志 | 凭据泄露 |
| P0-4 | 改 nginx 的 `listen 8080`、`/api/` 反代、`client_max_body_size 20m` | 学生端全部无法解析简历 |
| P0-5 | 删除 `/opt/backup`、`/root/*-backup-*`、`prev-*` 镜像 | 失去回滚能力 |
| P0-6 | 在服务器上 `npm run build` 构建前端 | 内存峰值，同 P0-1 |

---

## 2. 唯一允许的变更入口

```bash
# 只读
bash deploy/guard.sh status           # 内存/磁盘/容器/健康
bash deploy/guard.sh health           # /health/ready 明细
bash deploy/guard.sh logs api 200     # 容器日志

# 变更（均需 --yes）
bash deploy/guard.sh restart-api --yes
bash deploy/guard.sh safe-rebuild --yes      # 带内存门槛 + 自动回滚
bash deploy/guard.sh deploy-dist /root/dist.tar.gz --yes
bash deploy/guard.sh backup --yes
bash deploy/guard.sh verify           # 24 项验收
```

- **前端**：本地 `npm run build` → 打 tar → 上传 → `guard.sh deploy-dist`。禁止在服务器构建。
- **后端**：改代码 → 上传 → `guard.sh safe-rebuild`（自动停栈、内存检查、打 prev 标签、失败回滚）。
- **配置**：只改 `/opt/job-graph/.env`（600 权限），改完 `guard.sh restart-api`。

---

## 3. 交付口径

Agent 回答"完成"时必须包含：

| 项 | 要求 |
| --- | --- |
| 改了什么 | 文件路径 + 行为变化 |
| 证据 | 实际命令输出（HTTP 码、容器状态、字节数），不接受"应该可以" |
| 回滚点 | 具体路径或镜像标签 |
| 剩余风险 | 已知未解决问题 |

未跑验收就说"完成" → 视为未完成。

---

## 4. 权限与准入

- 无 RAM 子账号 AccessKey 者，**只能读仓库，不能碰服务器**。
- 需要服务器权限 → 找项目负责人申请（流程见手册 §7）。
- 拿到权限后先跑 `guard.sh status` 确认环境，再动手。

---

## 5. 遇到故障

先 `guard.sh status`，再按手册 §6 的决策树走。**不要边猜边改。**
不确定就停手，把 `status` 输出贴给负责人。
