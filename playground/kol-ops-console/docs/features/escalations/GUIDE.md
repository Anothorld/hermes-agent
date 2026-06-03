# 升级处理

## 功能说明

当 Agent 无法自动处理时创建 **升级工单**：操作员在升级台查看队列、填写结构化答复、预览草稿。可与活动或纯身份关联（`campaign_id` 可空）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/escalations`, `/escalations/:id` | `EscalationConsolePage.tsx` |
| `/replies` | `ReplyMonitorPage.tsx`（回复/升级深链监控） |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `EscalationConsolePage.tsx`, `ReplyMonitorPage.tsx`, `InboundEmailCard.tsx` |
| BE | `routers/escalations.py`, `events.py`（`/escalations/open` 等） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/escalations` | 队列 |
| POST | `/escalations` | 创建 |
| PATCH | `/escalations/{id}` | 更新状态/答复 |
| POST | `/escalations/{id}/preview-draft` | 预览回信 |

## 关联模块

- [kols](../kols/GUIDE.md) — 身份上下文
- [approvals](../approvals/GUIDE.md) — 升级后可能产生新草稿审批
- [live-events](../live-events/GUIDE.md) — 新升级 WS 推送

## 导航

全局 **升级**（`GlobalNav`）；未读点 `UnreadDot.tsx`。
