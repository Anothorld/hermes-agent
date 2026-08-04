# 活动（Campaign）

## 功能说明

单次 KOL 合作活动的全生命周期：解析/启动、配置、泳道快照、候选人池、发现门禁、跟进草稿、关闭、回复监听、合约就绪、Agent 日志流。Console 最大 router：`campaigns.py`。

## 操作员路径

| 路径 | 页面/组件 |
|------|-----------|
| `/products/:sku` | 启动活动、活动列表（`ProductDetailPage`） |
| `/campaigns/:id/candidates` | `CampaignCandidatesPage.tsx` |
| `/campaigns/:cid/transcript` | `AgentTranscriptPage.tsx` |
| 全局 | `shell/CampaignPicker.tsx`, `EditCampaignConfigPanel.tsx`, `NoxCampaignOpsPanel.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `ProductDetailPage.tsx`, `CampaignCandidatesPage.tsx`, `CampaignWizardPage.tsx`（legacy 重定向） |
| BE | `routers/campaigns.py`, `routers/candidates.py`, `routers/reply_watcher.py` |
| 核心 | `campaign_id_norm.py`, `campaign_locks.py`, `campaign_config_sync.py`, `discovery_gate.py`, `run_registry.py` |

## 主要 API（节选）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/campaigns/parse`, `/campaigns/start` | 创建/启动 |
| GET | `/campaigns/{id}/lanes` | 看板泳道数据（Bridge 批量读 CAL，**仅 shortlist 已批准**的 KOL；Console 约 8s 缓存） |
| GET/PATCH | `/campaigns/{id}/config` | 活动配置 |
| GET/POST | `/campaigns/{id}/candidates/*` | 发现池（GET 使用 handle JOIN，含 `total_collabs`） |
| GET | `/campaigns/{id}/shortlist` | 短名单（`social_links`、`link_previews` 按 URL、`preview_facts`、Nox、`prior_outreach_touch`、**`internal_touch_count`**（曾触达列表.xlsx，同指标页「内部曾触达次数」））；默认 **快速路径**（单次 `batch_facts_subset` + 缓存 OG）；`?prefetch_og=1` 为慢路径（逐人 `read_facts` +  live OG，供 Refresh）。**依赖 Bridge `GET /identities/internal-touch-count`** — 部署/更新 bridge 插件后需重启 bridge 进程，否则标签恒为 0。 |
| GET | `/campaigns/{id}/agent-stream` | SSE  transcript |
| POST | `/reply-watcher/*` | 入站回信监听（Bridge 内置 worker；`reconcile-sent` 为 SENT 对账）。产品页卡片展示 `running` / `enabled` / `inbound_disabled` 与 `last_tick_stats`（含 retry、deferred、errors） |

## 关联模块

- [kols](../kols/GUIDE.md) — 泳道卡片为 identity
- [agent-gateway](../agent-gateway/GUIDE.md) — 启动 Agent、SSE
- [nox](../nox/GUIDE.md) — 补充、统计
- [approvals](../approvals/GUIDE.md) — 活动内待发信审批

## 概念

- `campaign_id` + `env` 唯一标识活动
- **一个产品（SKU）+ env 只允许一个 campaign**（product ≡ campaign）：
  - `POST /campaigns/{id}/start` 对「同 SKU 已存在不同 campaign_id」硬性返回 409 `one_campaign_per_product`（**不可用 `force` 绕过**），响应含 `existing_campaign_id`；重启**同一** campaign_id 不受影响。
  - 产品页启动表单默认回填该 SKU 已有的 campaign_id。
  - `init_db` 在无历史重复时创建 `UNIQUE(sku, env)` 索引（`ux_product_campaigns_sku_env`）；存在历史重复则记 WARNING 并跳过，需先用合并工具清理。
  - **合并历史重复**：`playground/kol-ops-console/scripts/ops/merge_campaigns.py --source <旧> --target <留存> --env LIVE`（自动备份 app.db + cal.db；Bridge 侧走 `POST /campaigns/{target}/merge-from`，冲突时 target 行优先即保留操作员审批状态；Console 侧迁移 `product_campaign_runs`、删源行、写 `campaign.merge` audit）。
- 全局 `useCampaignStore` 决定 Kanban/审批过滤范围

## 发现数量门控与结构化续跑

实现模块：[`backend/app/discovery_gate.py`](../../../backend/app/discovery_gate.py)（`/rediscover` 与产品页 sync 后的 auto-retry 共用）。

| 机制 | 说明 |
|------|------|
| **数量门控** | 发现/rediscover run 结束后，比较 CAL 可见候选人数与 `product_campaigns.target_floor`；不足且 `retry_count < 5` 时自动再跑 rediscover。**同轮优先**：brief/`REDISCOVERY_INSTRUCTIONS` 要求在**当前 run** 内循环 ingest 直到 floor 或硬阻塞；auto-retry 只补硬阻塞后的缺口，不是「每轮入库 1 个」的默认路径 |
| **async 占位符** | 队列繁忙时 `/rediscover` 与 **auto-retry** 共用 `launch_or_accept`（202 + `pending:*`）；后台 launch 成功后必须把 placeholder 换成真实 `run_id`。`run_state_reconciler` 若发现 registry 中 `pending:*` 已结束，会将活动置为 `closed` 并把 `run_id` 还原为 registry 里最近一条非 pending 的 run |
| **diagnostics_history** | 每轮终态答案解析为 JSON 追加到 `product_campaigns.diagnostics_history`（`attempted_angles`、`next_round_focus`、`pending_ingests` 等） |
| **pending_ingests** | 已 qualify 但未 `ingest-confirmed-candidate` 的 handle；下一轮 brief 生成 `# resume_directives` + **STEP_0**（先入库再浏览） |
| **discovery bootstrap（gateway 强制）** | `kol-bridge-agent-guard` 在 discovery session 上 **阻塞** `browser_*` / `veedcrawl_*`，直到同 session 依次执行 bridge CLI：`list-candidates`、`skip-handles`、`cooldown-handles`；且 `--campaign-id` / `--env` 必须与 `kol-campaign:{env}:{campaign_id}` 一致（防 8010 session 调 8319） |
| **解析兜底** | 若 agent 未输出 YAML `pending_ingests:`，Console 从「Qualified but unpersisted」等段落启发式抽取（cap 5） |
| **排除已入库** | 已在 `list-candidates` 池中的 handle 不会出现在 `resume_directives` |
| **重置** | 操作员 `POST /campaigns/start` 将 `diagnostics_history` 置为 `[]` |

Agent 契约：skill `instagram-kol-discovery`（终态必须含 `pending_ingests` / `next_round_focus` 字段名，勿用「Next round should:」纯 prose）。Rediscover gateway instructions 含 `terminal_safety` + browser no-hang（单页单次、禁止并行 browser fan-out）。**内容筛选默认 text mode**（`KOL_RPA_VISION_EVAL_ENABLED=0`）：只采 caption + 评论，不跑 `vision_analyze` / 封面下载；重开视觉设 env=`1`。

**Launch brief 与 ingest 对齐（2026-06）：** `_LAUNCH_INSTRUCTIONS` step 4 要求 **`ingest-confirmed-candidate`**（嵌套 JSON），不再教 legacy `upsert-identity` → `add-candidate` 三步链。数量下限按 ingest 成功计数。Launch 阶段 **不** 跑全池 `route-discovery`（路由在 Console **批准短名单** 时 scoped 执行）。跨阶段 I/O 见 `agent_prj/docs/kol-pipeline-io-contracts.md`。

工程说明：[`agent_prj/docs/kol-discovery-auto-retry-resume.md`](../../../../../../../docs/kol-discovery-auto-retry-resume.md)。

## 数据源边界（避免混用）

| 用途 | 正确 API / 数据源 | 勿用 |
|------|-------------------|------|
| Shortlist review、产品页 `candidate_count` / `pending_candidate_count` | Bridge `list-candidate-handles`（排除 `rejected`/`archived`） | `get_lanes` |
| 看板泳道、outreach 草稿/发送进度 | `GET /campaigns/{id}/lanes`（仅 `selected_for_outreach` 等） | `list-candidate-handles` 当 kanban 列表 |
| 顶部 Campaign picker 的 `N kol` | Bridge `list-campaigns`（可见 shortlist 计数） | `get_lanes` 行数 |
| 发现数量门控 floor | `list-candidates` + `_count_visible_candidates` | `get_lanes` |

`get_lanes` **故意排除** `discovered` / `shortlisted` — 这些只在产品页 Shortlist review 出现。

## Shortlist 审批（操作员勾选）

### 显示与持久化

- **未批准的候选会一直留在 shortlist**，直到操作员手动移除；批准 shortlist **不会**自动隐藏或拒绝未勾选的行。
- `GET /campaigns/{id}/shortlist` 的 `candidates` **仅含待审批池**（`discovered` / `shortlisted` / `needs_review` 等，不含 `selected_for_outreach`）；已批准人数在 `counts.already_approved`，产品页提供 KOL 看板链接。`rejected` / `archived` 不出现在 `candidates`；`counts.rejected_or_archived_hidden` 为已隐藏数量。
- 产品页 Shortlist review 每行待审批候选有 **「从 shortlist 移除」**：弹出反馈弹窗（原因标签必选 + 评论，见下「决策学习反馈」），随后调用 `POST /campaigns/{id}/candidates/status`，将 `candidate_status` 设为 `rejected`（`review_reason=operator_removed_from_shortlist`，body 另带 `reason_tags[]` + `comment`）。**CAL 行仍在库中**，只是不再出现在 shortlist、也无法被勾选批准；指标页等全量视图仍可看到该发现记录。
- **「转到其他活动」**（Phase 1a，仅发现后、批准前）：`POST /identities/{identity_id}/transfer-campaign`，body 含 `from_campaign_id`、`to_campaign_id`、`env`、`reason`、`reason_tags[]`（标签必选；`reason` 即学习评论）。Bridge 将源行标为 `rejected`（`review_reason` 含 `transferred_to:<目标>`），在目标活动写入 `discovered` + `source=operator_transfer`，并 `resolve-relationships`。目标活动须已存在 `campaign_config`。若目标已有非 terminal 候选行则 409。CLI：`kol_bridge_tool.py transfer-campaign --identity-id … --from-campaign-id … --to-campaign-id …`。

### 决策学习反馈（批准 / 移除 / 转移共用）

- 三类操作都要求操作员打 **原因标签**（词表见 `GET /learning/discovery-tags`，按 action 过滤）并在**学习早期**（该 SPU 样本 < `KOL_DISCOVERY_COMMENT_MIN_SAMPLES`，默认 50）填写**真实理由评论**；达标后评论改为选填。
- 批准为批量操作：二级弹窗填写**本批共享**标签+评论；需对单个 KOL 说明不同理由时，在列表行点击 **点赞「标注」**（与「转到其他活动 / 从 shortlist 移除」同位置），填写后暂存于本页，点 Approve 时在二级弹窗**汇总展示并一并提交**（`decision_feedback.per_kol_overrides`，按 handle 键控）。「Retry draft run」重批已批准 KOL 不要求反馈（不是新决策）。
- 校验失败返回 422（`decision_feedback_required` / `decision_tags_required` / `decision_comment_required` / `decision_tags_invalid`——标签已失效或不存在，刷新词表后重选），不产生任何 CAL 副作用。词表不可达时跳过严格标签校验（降级，不阻塞）。
- 学习采集失败不回滚主操作：Console 端**重试一次**（请求路径上限 2 次尝试）后写 audit `learning.shortlist_decision_failed`（payload 含完整 `replay_body`），并在前端 toast 提示「样本未能记录，已留底备查」；可在学习页 Discovery 面板**一键补录**。
- 紧急关闭：`KOC_DISCOVERY_FEEDBACK_REQUIRED=false`——前后端同步生效（**跳过反馈弹窗**、后端跳过校验，不会卡住操作员）。
- 样本落 Bridge `shortlist_decision_learning` 事件并冻结 KOL 特征快照；夜间蒸馏与回灌见 [learning GUIDE](../learning/GUIDE.md#发现决策学习discover-闭环新增)。
- 活动启动 / rediscover 的 brief 末尾会附 `# learned_discovery_criteria`（已审批的 SPU/品类评判标准；开关 `KOC_DISCOVERY_LEARNED_CRITERIA`）。

### 批准流程

`POST /campaigns/{id}/approve-shortlist` 流程：

1. 将操作员勾选的 handle 解析为 `identity_ids`
2. **决策反馈校验（fail-fast）** — 对首次批准的 KOL 校验 `decision_feedback`（标签必选、早期评论必填），不通过返回 422 且无任何 CAL 副作用
3. **`route-discovery`（scoped）** — 仅对这批 `identity_ids` 写 `identity.outreach_path` 并 `select-candidates`；**不会**把池内其余 `discovered` 候选一并提升为已批准（**不再**重复调用 `select-candidates`，避免大批量超时）；随后 best-effort 写入决策学习事件（响应体 `learning`）
4. **缺邮箱自动排队** — 对每个 `primary_email` 为空的已批 identity，Console 同步跑 Nox Gate B（LIVE + `nox_quota_enabled`）后排队 `kol-email-discover:{env}:{id}`（与详情页「全网搜索」同路径）；Gate B 命中则跳过 browser。响应体含 `email_discovery[]`。
5. **创作者简介刷新排队** — 对每个 brief 缺失或超过 90 天 stale 的已批 identity，Console 排队 `kol-creator-brief-refresh:{env}:{identity_id}:{run_token}`（与 `# email_discovery_queued` 同模式，共享 browser 串行槽位）。**已在 `# email_discovery_queued` 中的 identity 不再单独排 brief**（由 `kol-email-discovery` Step 6 顺带刷新）。响应体含 `creator_brief_refresh[]`。Shortlist API / UI 返回 `creator_brief_ready` / `creator_brief_status`。
6. 写 `approved` 事件 + **带重试**拉起 post-approval gateway run（`bridge_approve_timeout_sec` 默认 180s；gateway 502/503/504 自动重试 2 次）；brief 含 `# email_discovery_queued` 与 `# creator_brief_queued`；outreach agent 对队列中的身份报告 `pending_email_discovery` / `pending_creator_brief`，**不** 在 outreach run 内开 browser。
7. **邮箱发现完成后自动起草** — `run_state_reconciler` 在 `kol-email-discover:*` run 到达 `completed` 且 `primary_email` 已写入、creator brief **已就绪**、该 identity 仍为 `selected_for_outreach`、尚无待审批草稿时，自动拉起单 KOL 的 `kol-campaign-draft:{env}:{campaign_id}:{identity_id}` run。audit 记 `campaign.auto_draft_after_email_discover`。若 Step 6 未刷新 brief，**补排队** `kol-creator-brief-refresh`（audit `kol.creator_brief.refresh_after_email_discover`），不在 brief 缺失时抢跑低个性化草稿。
8. **创作者简介刷新完成后自动起草** — 同上 reconciler 路径，session `kol-creator-brief-refresh:*` 完成且 brief ready 时触发（audit `campaign.auto_draft_after_creator_brief_refresh`）。适用于「有邮箱、缺 brief」的 approve 排队场景。

若 CAL 已更新但 gateway 启动失败，audit 会记 `campaign.approve_shortlist_gateway_failed`；操作员可再次点击批准（idempotent）或联系工程。

产品页「已批准 / 待审批」计数来自 CAL `candidate_status`（`selected_for_outreach` vs 其他）。若误批需用 bridge `set-candidate-status` 将多余行改回 `discovered`；若误移除 shortlist，可将 `rejected` 改回 `discovered` / `shortlisted`。

## Agent 短名单批准 run（outreach）

Console `POST …/approve-shortlist` 拉起 gateway run（session `kol-campaign-outreach:{env}:{id}`，与 discovery 的 `kol-campaign:` 分离）；brief 含 `bridge_cli_checklist`：

- 只用 **terminal** + **绝对路径** `kol-bridge-cli`（禁止 bare `python`、禁止相对 `plugins/…`、禁止 execute_code/curl）
- **禁止 browser / Chrome DevTools MCP** 在 outreach run 内做邮箱 enrichment 或创作者简介 active fetch（邮箱发现由 `kol-email-discover:*`、brief 刷新由 `kol-creator-brief-refresh:*` 在批准时排队；guard 在 outreach/reply/draft 前缀 block `browser_*`，**所有** `kol-*` session block `mcp_chrome_devtools_*`）
- 冷触达草稿：`persist-initial-outreach-draft`（稳定 `draft:outreach_{campaign}_{identity}`）

### 草稿正文必须是 HTML（POVISON 683 根因修复）

初邀/重起草稿正文是**直接写进 operator Gmail 草稿**的内容，必须是 HTML：每段 `<p>…</p>`，产品用真实 `<a href="<product_url>">…</a>` 链接，不能是纯文本营销段落，也不能是裸 URL。

- **技能层**：`kol-cold-outreach` / `kol-reengagement-outreach` 先跑 `kol-email-style-loader`（带 `--owner-user-id`）+ `kol-creator-brief-loader`，再 `humanizer`，输出 `html:true` + `kind:initial_outreach`。
- **Brief 层**：短名单批准 brief（`approved_by_user_id`）与 redraft brief（`requested_by_user_id`）均注入操作员 id，并要求 HTML + 产品链接 + style-loader/humanizer 流水线。
- **Bridge 兜底（确定性）**：`POST /reply-drafts/persist` 对初邀草稿调用 `reply_draft.to_html_email_body()`——纯文本会被自动包成 `<p>` 并把裸 URL 转成 `<a href>`，同时置 `html:true`；已是 HTML 则原样保留（幂等）。因此纯文本营销段落**不可能再被持久化/发送**；产品链接无法由 Bridge 凭空生成，仍由技能/brief 保证。
- **user_style**：`GET /policies/user_style` 无 `owner_user_id` 时返回空文档（不再 400），匹配 style-loader 的「无个人风格」回退。
- 门控插件：`kol-bridge-agent-guard`（**修改后必须重启 Hermes gateway**；匹配 `task_id`，非空 `session_id`）

详见 `agent_prj/docs/kol-bridge-agent-tooling.md`、skill `kol-cold-outreach`。

## 主动跟进草稿（operator-topic follow-up）

KOL 详情页 **主动跟进** → `POST /campaigns/{cid}/identities/{iid}/followup-draft`（skill `kol-proactive-followup`）。

| 前置条件 | 行为 |
|----------|------|
| `offer.outreach_sent=true` | 初邀已从 Gmail 发出；否则 409 `outreach_not_sent` |
| `approval.reply_draft.decision=pending` | 409 `pending_draft_exists` — 先去待审批处理 |
| 已审批的**回信**草稿仍在 Gmail 未 Send | 409 `approved_draft_exists`，需确认 `discard_existing_approved_draft=true` |
| 仅残留初邀审批记录（`child_skill=kol-cold-outreach` 等）且初邀已发出 | **不拦截** — 待审批页不会出现该历史记录，属正常 |

生成后进入待审批；**批准后**在**原 Gmail 线程**内创建回复草稿（`Re:` 主题 + 引用上一封），不是另起新邮件。Brief 会注入 `gmail_sent_thread_id` / `gmail_thread_id`；bridge `persist-reply-draft` 会把合成 thread 锚点替换为真实线程 ID。旧 Gmail 草稿（含 `offer.gmail_draft_id`）不会自动删除，重复批准前请手动清理草稿箱。

## 待审批草稿重生成（redraft-outreach）

KOL 详情页 **生成待审批草稿** → `POST /campaigns/{cid}/identities/{iid}/redraft-outreach`（skill `kol-cold-outreach` / `kol-reengagement-outreach`）。

Console 在拉起 gateway run **之前** 通过 bridge HTTP 读取 `get_campaign`、`get_identity(env)`、`get_dispatch_context`，并以 `# cal_snapshot` JSON 块嵌入 brief（agent view 瘦身后的 dispatch-context）。Agent **不必**再用 terminal 跑 `get-campaign` / `get-identity` / `get-dispatch-context`；brief 内 `redraft_cli_checklist` 仅列 persist 等写命令。Terminal 任何 bridge 子命令若 stdout 被 `>` 重定向会导致空输出（45 字符 wrapper）；guard 拦截 `> file`、`| head/grep/jq`，以及幻觉子命令（`read-identity`、`list-campaigns`）。`batch_ingest_files` block 的 `source: kol_bridge_agent_guard` 表示 guard 拦截，不是 bridge JSON 校验失败。

| 冲突码 | 含义 |
|--------|------|
| `campaign_run_in_flight` | 该 campaign 另有 agent run 在跑（approve、rediscover 等） |
| `redraft_inflight` | 同一 KOL 在 5 分钟内已触发过重生成 |
| `approved_draft_exists` | Gmail 里仍有已审批未发送草稿，需 `discard_existing_approved_draft=true` |
| `already_sent` | 初邀已发出，应走 follow-up |
| `gateway_concurrency_limit` (429) | Hermes Gateway 同时运行 run 数达上限（默认 10）。Console 会自动重试约 40s；仍失败时提示等待或在 Agent 会话面板停止不需要的任务 |

实现：`gateway_client.start_run_with_retry` + `gateway_http.http_exception_from_gateway_start`。

## 隐式同意与合同终局确认（2026-06）

全局默认开启（少数 campaign 可设 `strict_explicit_accept=true` 关闭）：

| 行为 | 操作员可见效果 |
|------|----------------|
| KOL 未明说 agree 但持续配合（选色、地址、timing 等） | 不再反复发「Does that work for you?」式 compensation 确认信 |
| 双方未谈 paid | 默认 `gifted` 置换路径 |
| 条款终局 | 发合同 = 最后一次确认；签署即代表同意附件中的 deliverables + compensation |

Bridge 确定性模块：`implicit_accept_policy.py`（classifier 写入后自动应用）。
Gmail 发出后会记录真实 `sent_body` 供策略判断。详见 `agent_prj/docs/kol-campaign-config-upsert.md` 中 policy 字段表。

**Console 配置入口**：产品详情页 → 「编辑 campaign_config」→ **合作确认策略** 区块（`implicit_accept_enabled` / `defer_terms_to_contract` / `strict_explicit_accept`）。

**结构化交付（Phase 2）**：启动 campaign Step C → **用自然语言描述交付内容** → **解析预览** → 确认表格后启动。写入 `campaign_deliverables_json`（含 ad code 等 extras）；未填 NL 时仍只用 platform checkbox，行为与旧版一致。合同 readiness 与 contract-coordinator 读 `GET …/resolved-deliverables` → `rows`。

**Console PATCH**（`EditCampaignConfigPanel`）可改 `implicit_accept_enabled` / `defer_terms_to_contract` / `strict_explicit_accept` 与 `campaign_deliverables_json`。

**`contract_required=false`（不签合同）**：仅跳过 **contract_signing** 目标；**不会**跳过 commerce，也不会在 KOL 尚未 agreed 时把卡片推进到 fulfillment（logistics / payout）。若历史数据误标 active，运行 `plugins/kol-ops-bridge/scripts/repair_fulfillment_goal_gate.py --scan`。

**运维 backfill**（历史线程缺 `offer.last_outbound_terms_proposed` 时）：

```bash
KOL_BACKFILL_DRY_RUN=1 python hermes-agent/plugins/kol-ops-bridge/scripts/backfill_outbound_terms.py
```
