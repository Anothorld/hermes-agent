# Frontend Components — 组件簇与功能模块

总架构：[../../../ARCHITECTURE.md](../../../ARCHITECTURE.md)

## 按目录

| 目录/文件 | 用途 | 模块 GUIDE |
|-----------|------|------------|
| `shell/` | 全局导航、TEST/LIVE、活动选择、面包屑 | 跨模块 |
| `agent-dock/` | Agent 会话浮层 | [agent-gateway](../../../docs/features/agent-gateway/GUIDE.md) |
| `gateway-approval/` | Gateway 命令审批浮层 + 顶栏徽章 | [agent-gateway](../../../docs/features/agent-gateway/GUIDE.md) |
| `dialogs/` | 确认/输入/归档弹窗 | 跨模块 |
| `feedback/` | Toast、错误提示 | §5 UI 规则 |
| `inputs/` | 事实输入、KOL 搜索 | [kols](../../../docs/features/kols/GUIDE.md) |
| `diff/` | 草稿 diff | [approvals](../../../docs/features/approvals/GUIDE.md) |

## 按业务组件

| 组件 | 模块 GUIDE |
|------|------------|
| `ApprovalDetailPanel`, `ApprovalContextCard`, `ApprovalActionBar`, `RejectCorrectionModal`, `DraftEditDiffPanel` | [approvals](../../../docs/features/approvals/GUIDE.md) |
| `LearningWorkflowStepper`, `LearningManualTriggerSection`, `StrategyPromotionPanel`, `OutcomePromotionPanel`, `LearningNextBatchPreview`, `LearningChannelTrends`, `PolicyMergeDiffPreview` | [learning](../../../docs/features/learning/GUIDE.md) |
| `LaneFilterBar`, `GoalProgressBar`, `FactsEditor`, `KolProfileDashboard`, `KolSocialQuickLinks`, `KolProfilePreviewLink`, `NoxDiligencePanel`, `NoxInsightsSections`, `NoxDistributionChart`, `KolRegistryTable`, `NoxAudienceHoverPanel`, `AudienceProfileHoverButton` | [kols](../../../docs/features/kols/GUIDE.md), [nox](../../../docs/features/nox/GUIDE.md), [gate-metrics](../../../docs/features/gate-metrics/GUIDE.md) |
| `EditCampaignConfigPanel`, `NoxCampaignOpsPanel`, `ContractReadinessPanel` | [campaigns](../../../docs/features/campaigns/GUIDE.md), [nox](../../../docs/features/nox/GUIDE.md) |
| `CommunicationHistoryPanel` | [gmail](../../../docs/features/gmail/GUIDE.md) |
| `InboundEmailCard`, `InboundEmailStack`, `EscalationSuggestedQuestion` | [escalations](../../../docs/features/escalations/GUIDE.md) |
| `AgentTranscriptPanel` | [agent-gateway](../../../docs/features/agent-gateway/GUIDE.md) |
| `factKeyLabel.ts` | [kols](../../../docs/features/kols/GUIDE.md) |

共享常量：`../constants/domainLabels.ts`, `../constants/rejectTags.ts`。
