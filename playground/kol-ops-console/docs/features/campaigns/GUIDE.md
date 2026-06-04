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
| GET | `/campaigns/{id}/lanes` | 看板泳道数据（Bridge 批量读 CAL；Console 约 8s 缓存） |
| GET/PATCH | `/campaigns/{id}/config` | 活动配置 |
| GET/POST | `/campaigns/{id}/candidates/*` | 发现池（GET 使用 handle JOIN，含 `total_collabs`） |
| GET | `/campaigns/{id}/shortlist` | 短名单（`social_links`、`link_previews` 按 URL、`preview_facts`、Nox、`prior_outreach_touch`） |
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

## Agent 短名单批准 run（outreach）

Console `POST …/approve` 拉起 gateway run；brief 含 `bridge_cli_checklist`：

- 只用 **terminal** + `kol_bridge_tool.py`（禁止 execute_code/curl/读 bridge 源码）
- 冷触达草稿：`persist-initial-outreach-draft`（稳定 `draft:outreach_{campaign}_{identity}`）
- 门控插件：`kol-bridge-agent-guard`（需重启 Hermes 后生效）

详见 `agent_prj/docs/kol-bridge-agent-tooling.md`、skill `kol-cold-outreach`。
