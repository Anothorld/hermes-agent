# KOL（身份 / 看板 / 详情）

## 功能说明

以 **identity_id** 为中心的 KOL 运营：8 阶段看板、详情页（事实、目标、邮件、发现、社交、Nox）、关系图、归档合作。状态权威在 Bridge；Console 聚合展示并触发 Agent。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/kols` | `KolKanbanPage.tsx`（默认首页 `/` 重定向至此） |
| `/kols/archive` | `KolArchivePage.tsx` |
| `/kols/:id` | `KolDetailPage.tsx`（看板读取 `identity.nox_*` 与通用 `identity.*` 事实） |
| `/kols/:id/relationship` | `KolRelationshipPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | 上表 pages；`KolProfileDashboard`, `KolNoxInsightsBoard`, `LaneFilterBar`, `GoalProgressBar`, `FactsEditor`, `CommunicationHistoryPanel`, `NoxDiligencePanel` |
| BE | `routers/kols.py`, `facts.py`, `goals.py`, `relationships.py` |
| 标签 | `components/factKeyLabel.ts`, `constants/domainLabels.ts` |

## 主要 API（节选）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/kols`, `/kols/{id}` | 列表/详情 |
| GET/PATCH | `/facts`, `/facts/multi` | CAL 事实 |
| GET | `/identities/{id}/goals` | 目标/泳道进度 |
| GET | `/kols/{id}/communication-history` | 绑定邮箱线程 |
| POST | `/kols/{id}/discover`, `/email/*`, `/nox/*` | 发现/邮件/Nox |

## 列表性能（看板 / 审批 / 详情）

| 场景 | 优化要点 |
|------|----------|
| 看板 `GET /campaigns/{id}/lanes` | Bridge 批量读 CAL（固定几次查询）；Console ~8s 缓存；深链 `?campaign_id=` 优先首屏 |
| 待审批 `GET /approvals` | Bridge JOIN `kol_identity` 带 `handle`；支持 `identity_id` + `campaign_id` 过滤 |
| 升级 `GET /escalations` | 支持同上过滤；列表 JOIN `handle` |
| KOL 详情 | 审批/升级请求带 `identity_id` + `campaign_id`，不再拉全表 |
| 产品短名单 / 多活动 | Nox 字段 `POST /facts/batch-subset`；产品页每活动只调一次 `lanes`；身份卡片 `POST /identities/briefs` |
| 候选人池 | `list_candidate_handles` + relationship JOIN，一次返回 handle/合作次数 |

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — `/campaigns/{id}/lanes` 驱动看板
- [approvals](../approvals/GUIDE.md) — `approval.*` 事实
- [escalations](../escalations/GUIDE.md) — 身份级升级
- [gmail](../gmail/GUIDE.md) — 邮箱绑定与历史
- [nox](../nox/GUIDE.md) — 尽调/联系人/监控

## Lane / Goal

泳道：`commerce`, `fulfillment`, `publish`, `meta`（类型见 `api.ts` `Lane`）。
