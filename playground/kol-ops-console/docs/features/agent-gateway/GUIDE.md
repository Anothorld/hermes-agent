# Agent 运行与 Gateway 审批

## 功能说明

- **Agent**：通过 Gateway 启动 Hermes run，登记 `run_id`，SSE 看 transcript，浮层 **Agent Session Dock** 多会话切换。
- **Gateway 审批**：YOLO/工具审批类提示，**Gateway Approval Dock** 全局浮层处理。

## 操作员路径

| 入口 | 组件/页面 |
|------|-----------|
| 活动页「启动 Agent」等 | `ProductDetailPage`, `KolDetailPage` |
| `/campaigns/:cid/transcript` | `AgentTranscriptPage.tsx`, `AgentTranscriptPanel.tsx` |
| 全局浮层 | `components/agent-dock/*`, `gateway-approval/GatewayApprovalDock.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `agent-dock/`, `AgentTranscriptPanel.tsx`, `hooks/useGatewayApprovals.ts` |
| BE | `gateway_client.py`, `run_registry.py`, `gateway_approval_watcher.py`, `routers/campaigns.py`（stream）, `routers/gateway_approvals.py` |
| 配置 | `bridge_runtime.py`（bridge key 注入 gateway env） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/campaigns/{id}/agent-stream` | SSE 日志 |
| GET/POST | `/gateway-approvals` | 列表/resolve |

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — 启动参数、brief
- [nox](../nox/GUIDE.md) — `nox_dispatch.py` 组 brief
- [live-events](../live-events/GUIDE.md) — gateway 审批 WS 事件

## 注意

Agent 状态 **不在** SQLite；以 Gateway + `run_registry` 为准。
