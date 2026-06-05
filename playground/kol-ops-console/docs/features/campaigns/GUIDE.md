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
| GET | `/campaigns/{id}/shortlist` | 短名单（`social_links`、`link_previews` 按 URL、`preview_facts`、Nox、`prior_outreach_touch`）；默认 **快速路径**（单次 `batch_facts_subset` + 缓存 OG）；`?prefetch_og=1` 为慢路径（逐人 `read_facts` +  live OG，供 Refresh） |
| GET | `/campaigns/{id}/agent-stream` | SSE  transcript |
| POST | `/reply-watcher/*` | Gmail 回复轮询 |

## 关联模块

- [kols](../kols/GUIDE.md) — 泳道卡片为 identity
- [agent-gateway](../agent-gateway/GUIDE.md) — 启动 Agent、SSE
- [nox](../nox/GUIDE.md) — 补充、统计
- [approvals](../approvals/GUIDE.md) — 活动内待发信审批

## 概念

- `campaign_id` + `env` 唯一标识活动
- 全局 `useCampaignStore` 决定 Kanban/审批过滤范围

## 发现数量门控与结构化续跑

实现模块：[`backend/app/discovery_gate.py`](../../../backend/app/discovery_gate.py)（`/rediscover` 与产品页 sync 后的 auto-retry 共用）。

| 机制 | 说明 |
|------|------|
| **数量门控** | 发现/rediscover run 结束后，比较 CAL 可见候选人数与 `product_campaigns.target_floor`；不足且 `retry_count < 5` 时自动再跑 rediscover |
| **diagnostics_history** | 每轮终态答案解析为 JSON 追加到 `product_campaigns.diagnostics_history`（`attempted_angles`、`next_round_focus`、`pending_ingests` 等） |
| **pending_ingests** | 已 qualify 但未 `ingest-confirmed-candidate` 的 handle；下一轮 brief 生成 `# resume_directives` + **STEP_0**（先入库再浏览） |
| **解析兜底** | 若 agent 未输出 YAML `pending_ingests:`，Console 从「Qualified but unpersisted」等段落启发式抽取（cap 5） |
| **排除已入库** | 已在 `list-candidates` 池中的 handle 不会出现在 `resume_directives` |
| **重置** | 操作员 `POST /campaigns/start` 将 `diagnostics_history` 置为 `[]` |

Agent 契约：skill `instagram-kol-discovery`（终态必须含 `pending_ingests` / `next_round_focus` 字段名，勿用「Next round should:」纯 prose）。

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
- `GET /campaigns/{id}/shortlist` 返回 `candidate_status` 不为 `rejected` / `archived` 的可见行；`counts.rejected_or_archived_hidden` 为已隐藏数量。
- 产品页 Shortlist review 每行待审批候选有 **「从 shortlist 移除」**：调用 `POST /campaigns/{id}/candidates/status`，将 `candidate_status` 设为 `rejected`（`review_reason=operator_removed_from_shortlist`）。**CAL 行仍在库中**，只是不再出现在 shortlist、也无法被勾选批准；指标页等全量视图仍可看到该发现记录。

### 批准流程

`POST /campaigns/{id}/approve-shortlist` 流程：

1. 将操作员勾选的 handle 解析为 `identity_ids`
2. **`route-discovery`（scoped）** — 仅对这批 `identity_ids` 写 `identity.outreach_path` 并 `select-candidates`；**不会**把池内其余 `discovered` 候选一并提升为已批准
3. `select-candidates` + 写 `approved` 事件 + 拉起 post-approval gateway run

产品页「已批准 / 待审批」计数来自 CAL `candidate_status`（`selected_for_outreach` vs 其他）。若误批需用 bridge `set-candidate-status` 将多余行改回 `discovered`；若误移除 shortlist，可将 `rejected` 改回 `discovered` / `shortlisted`。

## Agent 短名单批准 run（outreach）

Console `POST …/approve-shortlist` 拉起 gateway run；brief 含 `bridge_cli_checklist`：

- 只用 **terminal** + `kol_bridge_tool.py`（禁止 execute_code/curl/读 bridge 源码）
- 冷触达草稿：`persist-initial-outreach-draft`（稳定 `draft:outreach_{campaign}_{identity}`）
- 门控插件：`kol-bridge-agent-guard`（需重启 Hermes 后生效）

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
