# KOL（身份 / 看板 / 详情）

## 功能说明

以 **identity_id** 为中心的 KOL 运营：8 阶段看板、详情页（事实、目标、邮件、发现、社交、Nox）、关系图、归档合作。状态权威在 Bridge；Console 聚合展示并触发 Agent。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/kols` | `KolKanbanPage.tsx`（默认首页 `/` 重定向至此） |
| `/kols/archive` | `KolArchivePage.tsx` |
| `/kols/:id` | `KolDetailPage.tsx` |
| `/kols/:id/relationship` | `KolRelationshipPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | 上表 pages；`LaneFilterBar`, `GoalProgressBar`, `FactsEditor`, `CommunicationHistoryPanel` |
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

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — `/campaigns/{id}/lanes` 驱动看板
- [approvals](../approvals/GUIDE.md) — `approval.*` 事实
- [escalations](../escalations/GUIDE.md) — 身份级升级
- [gmail](../gmail/GUIDE.md) — 邮箱绑定与历史
- [nox](../nox/GUIDE.md) — 尽调/联系人/监控

## Lane / Goal

泳道：`commerce`, `fulfillment`, `publish`, `meta`（类型见 `api.ts` `Lane`）。
