# Frontend Pages — 路由与功能模块对照

路由定义：`../App.tsx` · 总架构：[../../../ARCHITECTURE.md](../../../ARCHITECTURE.md)

| 页面文件 | 路由 | 功能模块 GUIDE |
|----------|------|----------------|
| `LoginPage.tsx` | `/login` | [auth-settings](../../../docs/features/auth-settings/GUIDE.md) |
| `SettingsPage.tsx` | `/settings` | [auth-settings](../../../docs/features/auth-settings/GUIDE.md), [gmail](../../../docs/features/gmail/GUIDE.md) |
| `ProductListPage.tsx` | `/products` | [products](../../../docs/features/products/GUIDE.md) |
| `ProductDetailPage.tsx` | `/products/:sku` | [products](../../../docs/features/products/GUIDE.md), [campaigns](../../../docs/features/campaigns/GUIDE.md), [nox](../../../docs/features/nox/GUIDE.md) |
| `KolKanbanPage.tsx` | `/kols` | [kols](../../../docs/features/kols/GUIDE.md) |
| `KolArchivePage.tsx` | `/kols/archive` | [kols](../../../docs/features/kols/GUIDE.md) |
| `KolDetailPage.tsx` | `/kols/:id` | [kols](../../../docs/features/kols/GUIDE.md), [nox](../../../docs/features/nox/GUIDE.md), [gmail](../../../docs/features/gmail/GUIDE.md) |
| `KolRelationshipPage.tsx` | `/kols/:id/relationship` | [kols](../../../docs/features/kols/GUIDE.md) |
| `CampaignCandidatesPage.tsx` | `/campaigns/:id/candidates` | [campaigns](../../../docs/features/campaigns/GUIDE.md) |
| `AgentTranscriptPage.tsx` | `/campaigns/:cid/transcript` | [agent-gateway](../../../docs/features/agent-gateway/GUIDE.md) |
| `ApprovalsPage.tsx` | `/approvals` | [approvals](../../../docs/features/approvals/GUIDE.md) |
| `LearningPage.tsx` | `/learning` | [learning](../../../docs/features/learning/GUIDE.md) |
| `EscalationConsolePage.tsx` | `/escalations`, `/escalations/:id` | [escalations](../../../docs/features/escalations/GUIDE.md) |
| `ReplyMonitorPage.tsx` | `/replies` | [escalations](../../../docs/features/escalations/GUIDE.md) |
| `PolicyEditorPage.tsx` | `/policies` | [policies](../../../docs/features/policies/GUIDE.md) |
| `GateMetricsPage.tsx` | `/metrics` | [gate-metrics](../../../docs/features/gate-metrics/GUIDE.md) |
| `CampaignWizardPage.tsx` | （legacy，重定向 `/products`） | [campaigns](../../../docs/features/campaigns/GUIDE.md) |

默认首页：`/` → `/kols`。
