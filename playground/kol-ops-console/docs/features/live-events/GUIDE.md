# 实时事件（WebSocket）

## 功能说明

浏览器通过 **WebSocket** 订阅 Bridge 事件与 Gateway 审批事件，驱动页面增量刷新，减少轮询。Vite 开发时将 `/ws` 代理到后端 8765。

## 操作员感知

多数页面 **自动刷新**（新审批、升级、lane 变化），无需手动刷新；WS 断开时 hooks 可降级轮询。

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `useLiveEvents.ts`, `hooks/useDataChannel.ts`, `hooks/usePollingFallback.ts` |
| BE | `routers/events.py`（`WebSocket /ws`、hub） |
| 相关 | `gateway_approval_watcher.py` → hub |

## 协议要点

- 连接：`ws://<host>:8765/ws?token=<JWT>`
- 鉴权：query token（浏览器 WS 无法带 Authorization header 时的惯例）
- 事件类型：bridge `event_type`、gateway approval 等（页面按类型 invalidate）

## 关联模块

- 几乎所有列表页：[approvals](../approvals/GUIDE.md), [escalations](../escalations/GUIDE.md), [kols](../kols/GUIDE.md)
- [agent-gateway](../agent-gateway/GUIDE.md) — 审批 dock

## 开发

`frontend/vite.config.ts` proxy `/ws` → backend。
