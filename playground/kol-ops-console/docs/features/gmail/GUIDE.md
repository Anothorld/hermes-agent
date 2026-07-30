# Gmail（操作员邮箱）

## 功能说明

每位操作员 **独立 OAuth** 连接 Gmail；首次批准回信时绑定 `offer.gmail_mailbox_*` 事实。Bridge 发信时经 `/internal` 取 token 文件路径。支持邮箱接管（takeover）。

**Bridge 连接 Console：** poller 默认 `KOC_CONSOLE_BASE=http://127.0.0.1:8765`（与 Console `KOC_PORT` 一致）。若 Bridge 连不上 Console，会回退到本地 `gmail_tokens/*.json` 或 legacy token；占位邮箱名（`legacy`、`@imported.local`）会解析为 Gmail profile 真实地址，避免误报 `inbound_mailbox_mismatch`。

## 操作员路径

| 路径 | 场景 |
|------|------|
| `/settings` | 连接/断开 Gmail |
| `/kols/:id` | `CommunicationHistoryPanel` 查看线程 |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `SettingsPage.tsx`, `CommunicationHistoryPanel.tsx` |
| BE | `routers/google_auth.py`, `routers/internal.py`, `gmail_store.py`, `gmail_token_crypto.py` |
| 脚本 | `scripts/gmail_multimailbox_setup.py` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/google/start`, `/callback` | OAuth |
| GET/DELETE | `/auth/google/status` | 状态/断开 |
| GET | `/internal/gmail-token-path` | Bridge 专用（`X-Internal-Key`） |
| POST | `/kols/{id}/takeover-mailbox` | 接管绑定 |

## Gmail 后台 worker（Bridge）

`gmail_worker` 为默认 coordinator：按各自 interval 到期后 **串行** 跑入站 → SENT（见 [performance](../performance/GUIDE.md) env 表）。

| 路径 | 职责 | 入口 |
|------|------|------|
| SENT 对账 | 操作员在 Gmail 点 Send 后标记已发 + edit-learning | `gmail_poller` tick / `POST /gmail/reconcile-sent` |

SENT 对账护栏（2026-06-11 / 2026-07-30 修订）：
- **退信 DSN**（`mailer-daemon` / `Address not found`）不会写入 `draft_edit_learning`，也不会把 DSN 正文写成 `offer.last_outbound_terms_proposed`。
- 一旦 Gmail 线程已出现在 SENT（操作员点过 Send），对账仍会写 `offer.outreach_sent` + `offer.outreach_sent_at`，避免看板卡在「Draft 待发送」。Edit-learning 与 delivery 标记解耦。
- 已有 `offer.outreach_sent_at` 时，入站分类器不得再用 `email:<message_id>` 把 `offer.outreach_sent` 写回 false（避免 reconcile 每 9 分钟重复跑同一 approved 稿）。
- `list_approved_reply_drafts` 在 `outreach_sent_at` 已存在时跳过，即使 `outreach_sent` 被误写成 false。
- **未绑定邮箱 first-claim**：多操作员 Console 下，尚未 bind 的 campaign 可由「已在 SENT 中看到该线程」的操作员邮箱完成对账并 bind（不再要求 `user_id==0`）。
- 对账结果返回 `skip_reasons`；产品页「SENT 同步」使用当前页面 env（默认 LIVE），toast 展示跳过原因。

**Note:** `run_reconcile_all_mailboxes` must call the unlocked reconcile helper while already holding `gmail_reconcile.lock`; re-entering the lock deadlocks the bridge worker and leaves cards stuck on「Draft 待发送」。
| 入站回信 | 扫 INBOX → `kol_inbound_reply` → gateway dispatch | `gmail_inbound_poller` tick |
| 运维快照 / one-shot | 最近 tick、手动跑一次 | `GET /gmail/worker/status`、`POST /gmail/inbound-poller/run-once` |

入站实现：[`plugins/kol-ops-bridge/inbound_reply/`](../../../plugins/kol-ops-bridge/inbound_reply/)（worker in-process；CLI HTTP）。

Console 产品页 **回信监听** 卡片展示：`running` / `enabled` / `inbound_disabled`、最近一轮
`last_tick_stats`（matched / retry / deferred / errors）及 `last_error`。

Gateway 重试退避 env：`KOL_OPS_INBOUND_GATEWAY_RETRY_BASE_SEC`（默认 60）、
`KOL_OPS_INBOUND_GATEWAY_RETRY_MAX_SEC`（默认 3600）。

## 关联模块

- [approvals](../approvals/GUIDE.md) — 发信触发
- [campaigns](../campaigns/GUIDE.md) — 产品页回信监听 UI
- [performance](../performance/GUIDE.md) — env 开关与锁说明
- 文档：`agent_prj/docs/kol-operator-gmail-onboarding.md`

## 安全

Token 静态加密存储；勿在 UI 展示 token 或文件路径。
