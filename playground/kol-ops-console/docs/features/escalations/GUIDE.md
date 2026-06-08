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

## Agent 恢复 run（Resume）约束

Console 在 `resolve`（decision=resume）或「预览草稿」时会拉起 gateway run，
`session_id` 一般为 `kol-campaign:{env}:{campaign_id}`。

**硬性要求（`bridge_agent_contract.py` + `routers/escalations.py`）：**

- 只能使用绝对路径 **`kol-bridge-cli`**（`# terminal_safety`），禁止 bare `python`、相对 `plugins/…`
- 使用原生 **terminal**（每条命令一个子命令），禁止 `execute_code`+subprocess / `curl`
- Brief 含 `requested_by_user_id` → `get-email-conversation --operator-user-id` 与 `kol-email-style-loader`
- Brief 内含 `# bridge_cli_checklist` 有序步骤（resume / preview-draft）
- 草稿预览/需回信 resume：先 `kol-email-style-loader` + `kol-creator-brief-loader` + `humanizer`，再按目标 skill 的 format contract 写草稿
- CLI 失败在 **stdout** 输出 JSON；空 output + exit 2 读 stdout，禁止转 `execute_code`
- 禁止硬编码 `BRIDGE_KEY`；禁止读/搜 `plugins/kol-ops-bridge/` 源码
- 邮件线程：**`get-email-conversation`**；草稿：**`persist-reply-draft`** 或 `write-facts`
- Hermes 插件 **`kol-bridge-agent-guard`** 在 `pre_tool_call` 拦截违规（`KOL_BRIDGE_AGENT_GUARD=0` 可关）

详见：`agent_prj/docs/kol-bridge-agent-tooling.md`

## 关联模块

- [kols](../kols/GUIDE.md) — 身份上下文
- [approvals](../approvals/GUIDE.md) — 升级后可能产生新草稿审批
- [live-events](../live-events/GUIDE.md) — 新升级 WS 推送

## 导航

全局 **升级**（`GlobalNav`）；未读点 `UnreadDot.tsx`。
