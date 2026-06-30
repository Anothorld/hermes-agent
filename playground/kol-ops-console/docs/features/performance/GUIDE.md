# Performance — Agent 爆发期治理

面向操作员与运维：如何在 **Gateway 10-run 硬顶** 不变的前提下，让 Console 在大量 Agent 并发时保持可预测。

## 问题画像

| 症状 | 常见原因 |
|------|----------|
| 启动 Agent 后长时间 429 / 无响应 | Gateway 槽位满；未排水 |
| 四路 email-discover 同时挂死数小时 | 浏览器 discover 并行占槽 |
| 产品页刷新变慢 | GET 曾触发 gateway 轮询（已后台化） |
| bridge.log 报 `unable to open database file` | CAL 争用 / EMFILE |
| bridge.log 大量 `gmail call timed out` | Google API 慢 / 30s 硬超时（已默认 60s + 1 次重试） |
| learning job 长期 `status=running` | Gmail 任务中断未 finish；定时 batch 会自动 reconcile |

## Bridge 侧 Gmail / Learning 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `KOL_GMAIL_TIMEOUT_SEC` | `60` | `google_api.py` 子进程超时（原 30s） |
| `KOL_GMAIL_MAX_RETRIES` | `1` | 超时后自动重试次数 |
| `KOL_LEARNING_STALE_RUNNING_HOURS` | `2` | 超过此时间的 `running` learning job 在下次 batch 开头标为 `error` |

## Console 侧机制（已实现）

| 机制 | 配置项 | 说明 |
|------|--------|------|
| Run 启动队列 | `KOC_GATEWAY_LAUNCH_QUEUE_ENABLED`（默认 true） | 上限 `KOC_GATEWAY_LAUNCH_MAX_INFLIGHT=8` |
| Email discover 串行 | 队列 `kind=email_discover` max=1 | 全局仅 1 路 `kol-email-discover:*`；worker 在 `start_run` 返回 run_id 后**仍持有 semaphore** 直至 SSE drain 结束（HTTP/202 不阻塞整段 browser run）；**队列关闭时** bypass 路径同样 serial + drain |
| Creator brief refresh 串行 | 与 email discover 共享 browser semaphore | `kind=creator_brief_refresh`（`kol-creator-brief-refresh:*`）与 email discover **共用** 全局 browser 槽（max 1），避免并行 Chrome |
| Recovery 串行 | `KOC_RECOVERY_LAUNCH_SERIAL=true` | `recovery-*` session 不 fan-out |
| SSE 排水 | 自动 | 非 email-discover：`ensure_run_drained` 后台排水；email-discover 由队列 worker 同步 drain（不重复） |
| Run 状态后台对齐 | `KOC_RUN_RECONCILER_ENABLED=true` | GET 默认只读缓存 |
| Approval watcher 降级 | `KOC_APPROVAL_WATCH_MODE=auto` | open runs >5 时 poll_aggregate |
| Transcript SSE 上限 | `KOC_AGENT_STREAM_MAX_RUNS=10` | 每 campaign 并行 SSE 代理数 |
| Nox 子进程 | `KOC_NOX_MAX_CONCURRENT=2` | diligence batch 有限并行 + `to_thread` |
| WS 增量 poll | `events.py` `since_id` | bridge 读从全量降为增量 |
| 运行时指标 | `GET /admin/perf-snapshot` | 队列深度、`launch_queued_total`、WS 客户端、reconciler |
| Launch HTTP 202 | `KOC_LAUNCH_HTTP_202=true`（默认） | 队列繁忙时 campaign/discover/rediscover 立即 202，轮询 `GET /campaigns/launch-jobs/{id}` |
| 队列 dedup | `run_launch_queue` | 相同 `dedup_key` 的并发请求合并为一次等待 |
| Bridge 预检 | `KOC_LAUNCH_BRIDGE_HEALTH_CHECK=true` | worker 出队前 `GET /health`，失败快速 502 |
| 手动对齐 | `POST /products/reconcile-runs?env=` | 操作员强制刷新 run 状态 |
| Discovery brief 减重 | `KOC_BRIEF_COMPACT_CONTRACT=true`（默认） | 发现类 run 用紧凑 bridge 规则，省 ~1k token |
| Nox batch 异步 | `KOC_NOX_BATCH_ASYNC=true`，`KOC_NOX_BATCH_ASYNC_MIN_IDS=5` | 大批量 Gate A 返回 202 + `GET /kols/jobs/{id}` |
| 慢 API 结构化日志 | `KOC_SLOW_API_LOG_ENABLED=1` | 写入 `perf_snapshot.slow_api_recent`，含 `campaign_id`/`identity_id` |
| Gateway 审批单例 | `GatewayApprovalProvider.tsx` | 全应用共享一次 `/gateway-approvals` snapshot |
| `write_facts_multi` 单次 recompute | bridge `cal.py` | 多 namespace 写入只触发一次 goal 重算 |
| 发现决策反馈门 | `KOC_DISCOVERY_FEEDBACK_REQUIRED=true`（默认） | 批准/移除/转移要求标签+早期评论；false 为紧急回滚（停止学习采集） |
| Learned criteria 注入 | `KOC_DISCOVERY_LEARNED_CRITERIA=true`（默认），`KOC_DISCOVERY_LEARNED_CRITERIA_MAX_CHARS=4000` | 启动/rediscover brief 末尾附已学评判标准；bridge 不可用时跳过不阻塞 launch |

Bridge 侧发现学习相关：`KOL_DISCOVERY_COMMENT_MIN_SAMPLES=50`（早期评论必填阈值，SQL COUNT 精确计数无上限误判）、`KOL_DISCOVERY_LEARNING_BATCH_SIZE=10`（每组蒸馏批次）、`KOL_DISCOVERY_TAG_MINE_MIN_COUNT=5`（标签挖掘最小频次）。蒸馏/挖掘均为夜间 LIVE-only 批任务（`learning_llm` 直连，不占 gateway 并发）；采集挂在既有 POST 上、GET 路径零副作用（`/learning/discovery-feedback-requirements` 在 bridge 不可达时降级返回而非 502）。采集失败降级不回滚主操作：请求路径重试上限 2 次尝试（防 bridge 挂起拖长操作员请求），失败留 audit `replay_body`；学习页 `POST /learning/replay-shortlist-capture` 一键补录（幂等标记 `replayed_at`）。决策校验在请求路径多 2 个 bridge 轻量 GET（标签词表 + 评论必填），词表不可达时跳过严格校验。

操作员可在 **Agent Session Dock** 看到「排队 N」提示（`GET /campaigns/run-launch-status`）。

## 压测脚本

```bash
# Gateway 槽位饱和（需 JWT + campaign）
python scripts/load/bench_gateway_slots.py \
  --token "$KOC_JWT" --campaign-id YOUR-CAMPAIGN --count 12

# Bridge 批量读（产品 summary + lanes）
python scripts/load/bench_bridge_batch.py \
  --token "$KOC_JWT" --env TEST --rounds 20

# Locust（需 pip install locust）
export KOC_JWT="$KOC_JWT"
locust -f scripts/load/locustfile.py --host http://127.0.0.1:8765
```

## 日志健康探针

```bash
python scripts/health/check_kol_perf.py
```

检查项：

- `Tab pool acquired` 后 120s 无 `browser_navigate completed` → WARN
- bridge `unable to open database file` ≥3 → WARN
- bridge `gmail call timed out` 聚集 → WARN（可调 `KOL_GMAIL_TIMEOUT_SEC` / 查 Google token）
- `mcp_chrome_devtools_*` 错误聚集 → WARN
- run `api_calls=90/90` → WARN

**日志路径（重要）**

| 日志 | 路径 |
|------|------|
| KOL Agent run | `~/.hermes/profiles/kol-orchestrator/logs/agent.log` |
| Bridge | `~/.hermes/kol-ops-bridge/bridge.log`（已轮转 10MB×5） |
| Console | uvicorn stdout |

默认 `~/.hermes/logs/` **不含** KOL orchestrator run 详情。

## 运维 Runbook（不改 Hermes core）

### 1. 清理死 MCP（POVISON 批次实证）

在 `kol-orchestrator` profile 的 `config.yaml` 中**删除** `chrome_devtools` MCP 条目，重启 Gateway。

确认 brief/技能禁止 `mcp_chrome_devtools_*`（`kol-bridge-agent-guard` 纵深防御）。

### 2. 浏览器工具链

```bash
npm install -g agent-browser
```

Profile 设置 `browser.cloud_provider: local`。Chrome 长跑后：

```bash
# 视本地脚本路径而定
./start-debug-chrome.sh restart
```

爆发前可执行 tab-pool 运维：`reap_orphan_blank_tabs`（见 `local-chrome-tab-pool` README）。

### 3. Bridge 崩溃 / EMFILE

```bash
ulimit -n 4096
```

检查 `~/.hermes/kol-ops-bridge/cal.db-wal` 权限；重启 bridge：

```bash
python plugins/kol-ops-bridge/serve.py --port 8080
```

Gmail 后台由 **单一 coordinator** `gmail_worker.py` 驱动（默认），每轮 **串行** 执行：入站 INBOX tick → SENT 对账 tick，避免两路同时打 Gmail API。

| 任务 | 模块 | 禁用 env |
|------|------|----------|
| 统一调度 | `gmail_worker.py` | — |
| SENT 对账 | `gmail_poller.run_sent_tick_*` | `KOL_OPS_BRIDGE_DISABLE_GMAIL_POLLER=1` |
| 入站回信 | `gmail_inbound_poller.run_tick_*` | `KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER=1` |

- 调度状态：`~/.hermes/kol-ops-bridge/gmail_worker.json`（最近 tick 时间）；入站配置：`inbound_poller.json`。
- SENT 路径（定时 + `POST /gmail/reconcile-sent` + learning job）共用 `gmail_reconcile.lock`。
- 入站逻辑在 [`inbound_reply/`](../../../plugins/kol-ops-bridge/inbound_reply/)（worker 默认 in-process `cal.*`）；CLI `scripts/kol_reply_dispatcher.py` 走 HTTP。
- 观测：`GET /gmail/worker/status`；运维 one-shot：`POST /gmail/inbound-poller/run-once`。
- 回退：`KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT=1`（旧 monolith）；`KOL_OPS_GMAIL_WORKER_PARALLEL=1`（并行 poller）。
- 其它 env：`KOL_OPS_GMAIL_WORKER_WAKE_SEC`（默认 5）、`KOL_OPS_GMAIL_INBOUND_AUTO_START`（默认 **0**，需 Console 启停或显式设 1）。

### 4. 调优开关速查

```bash
export KOC_GATEWAY_LAUNCH_MAX_INFLIGHT=8
export KOC_AGENT_STREAM_MAX_RUNS=10
export KOC_APPROVAL_WATCH_MODE=auto
export KOC_SYNC_RUN_STATES_ON_GET=false   # GET 无副作用（默认）
export KOC_SLOW_API_LOG_ENABLED=1         # 开发期慢 API 探针
export KOC_LAUNCH_HTTP_202=true           # 队列忙时 HTTP 立即 202（默认）
export KOC_LAUNCH_BRIDGE_HEALTH_CHECK=true
```

## 关联代码

| 模块 | 路径 |
|------|------|
| 启动队列 | `backend/app/run_launch_queue.py` |
| 排水 / 入队 | `backend/app/gateway_client.py` |
| 后台 reconciler | `backend/app/run_state_reconciler.py` |
| 指标 | `backend/app/perf_snapshot.py` |
| Approval watcher | `backend/app/gateway_approval_watcher.py` |
| Bridge CAL 批量写 | `plugins/kol-ops-bridge/discovery_router.py` |
| 前端 WS 单例 | `frontend/src/LiveEventsProvider.tsx` |
| 审批 snapshot 单例 | `frontend/src/GatewayApprovalProvider.tsx` |
| 后台 job 存储 | `backend/app/background_jobs.py` |
| Launch accept + rollback | `backend/app/launch_accept.py`, `launch_rollback.py` |
| FE launch poll | `frontend/src/lib/launchJobs.ts` |
| agent-stream SSE 计数 | `perf_snapshot.open_gateway_sse_count` | 当前 campaign transcript 并行 gateway SSE 数 |
| Locust 压测 | `scripts/load/locustfile.py` | 需 `pip install locust` + `KOC_JWT` |

## 验收场景（摘要）

- **S1**：连续 10+ 启动 → 429 率 <5%，第 11 个 P95 排队 <30s
- **S5**：连续 4 次 discover-email → 仅 1 路 running，其余排队
- **S3**：爆发 + 产品页轮询 → `get_run` 调用量下降 >80%

详见计划文档 Phase 4 压测章节。
