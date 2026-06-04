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

## Agent 短名单批准 run（outreach）

Console `POST …/approve` 拉起 gateway run；brief 含 `bridge_cli_checklist`：

- 只用 **terminal** + `kol_bridge_tool.py`（禁止 execute_code/curl/读 bridge 源码）
- 冷触达草稿：`persist-initial-outreach-draft`（稳定 `draft:outreach_{campaign}_{identity}`）
- 门控插件：`kol-bridge-agent-guard`（需重启 Hermes 后生效）

详见 `agent_prj/docs/kol-bridge-agent-tooling.md`、skill `kol-cold-outreach`。
