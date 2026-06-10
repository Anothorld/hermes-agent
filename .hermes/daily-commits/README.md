# 每日 Git 提交汇总

## 2026-06-09（周二）— 9 次提交

### 当日进度概览

6 月 9 日完成了 KOL-Ops Bridge 的重大架构拆分与性能优化，将 Gmail 入站轮询从单体分发器抽离为独立模块，同时统一了 Gmail Worker 和 Console 启动性能，引入异步启动队列（HTTP 202）。此外新增 mysql-tools 插件，实现 clarify 审批门控与 shortlist 活动迁移，并修复了多条网关重试、升级回复、飞书审批卡片等关键问题。

### 重点变更

#### 🏗️ KOL-Ops Bridge 架构重构
- **inbound_reply 模块抽离**（29bdb82e）：将 Gmail 入站轮询从单体 dispatcher 拆为 inbound_reply/ 独立模块，支持进程内和 HTTP 两种端口，精简 CLI，保留 legacy 回滚路径；42 文件，+3992/−1396
- **global-seen 恢复与网关重试退避**（ec2f07a9）：为 globally-seen 消息添加 should_retry_gateway_only 恢复逻辑，指数退避重试，延迟统计，Console watcher 状态优化；15 文件，+407/−27

#### ⚡ KOL-Ops 性能与统一
- **Gmail Worker + Console 启动性能**（4e27c380）：合并入站回复轮询到 bridge serve，串行 gmail_worker 协调器，异步启动队列 HTTP 202，run reconciler，性能调优工具；72 文件，+4633/−1171
- **Shortlist 内部触达计数**（b39b23ee）：新增 bridge batch 端点，shortlist 展示 identity 和 handle-only 候选的内部触达次数；10 文件，+225/−3
- **Pending Run 守卫与启动去重**（10ffc5a93）：跳过 pending 占位符避免异步 202 启动被提前关闭，重放 pending_run_id，默认关闭入站轮询自动启动；7 文件，+57/−12

#### 🗄️ MySQL Tools 插件
- **Clarify SQL 门控 + Shortlist 活动迁移**（ca92f09e7）：新增 mysql-tools 插件，clarify 审批门控、直 SQL 旁路守卫、mysql-nl2sql 技能；Phase 1a shortlist 活动跨 bridge API/Console UI/CLI 迁移；34 文件，+2477/−14

#### 🔧 Gateway 与升级修复
- **飞书审批卡片 & MySQL Clarify**（58f45fb09）：规范化飞书卡片按钮值，open_id/user_id 双通道授权，mysql clarify 通过运行中 agent 解析；4 文件，+228/−19
- **升级回复合并**（361ba214c）：将 kol_inbound_reply 追加到最佳 inbound-tagged awaiting_answer 行，修复 trigger 角色种子，Console 展示追信；14 文件，+521/−15
- **Nox 批量配额门控 & 升级回复加固**（4773b9ef9）：加载 campaign_config 暴露 nox_quota_enabled，分离 action 错误与 shortlist 加载失败，升级入站栈、草稿守卫、追信延迟规则；33 文件，+1884/−151

### 提交明细

| 短 Hash | 时间 | 提交信息 |
|---------|------|---------|
| b39b23ee4 | 18:33 | feat(kol-ops): batch internal touch count on shortlist |
| 58f45fb09 | 18:16 | fix(gateway): Feishu approval cards and mysql clarify via running agent |
| ec2f07a9a | 17:42 | fix(kol-ops-bridge): global-seen recovery and gateway retry backoff |
| 29bdb82ee | 17:16 | feat(kol-ops-bridge): extract inbound_reply module with resilient failure recovery |
| 10ffc5a93 | 16:39 | fix(kol-ops): guard pending runs and align launch dedup |
| 4e27c3807 | 16:31 | feat(kol-ops): unify Gmail worker and Console launch performance |
| ca92f09e7 | 15:42 | feat(mysql-tools,kol): add clarify SQL gate and shortlist campaign transfer |
| 361ba214c | 10:55 | fix(escalations): merge follow-up inbounds into one open escalation |
| 4773b9ef9 | 10:29 | fix(kol-ops): gate Nox batch on quota config and harden escalation reply flow |

### 变更统计

- **文件数**：231 个文件变更
- **新增行**：+14,424
- **删除行**：−2,808
- **净增**：+11,616
