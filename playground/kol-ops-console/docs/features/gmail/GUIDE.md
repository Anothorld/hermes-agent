# Gmail（操作员邮箱）

## 功能说明

每位操作员 **独立 OAuth** 连接 Gmail；首次批准回信时绑定 `offer.gmail_mailbox_*` 事实。Bridge 发信时经 `/internal` 取 token 文件路径。支持邮箱接管（takeover）。

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
| 入站回信 | 扫 INBOX → `kol_inbound_reply` → gateway dispatch | `gmail_inbound_poller` tick |
| 运维快照 | 最近 tick、interval、入站启停 | `GET /gmail/worker/status` |

Console `POST /reply-watcher/start|stop|restart` → Bridge `POST /gmail/inbound-poller/*`（无独立子进程）。SENT 手动同步：`POST /reply-watcher/reconcile-sent`。

## 关联模块

- [approvals](../approvals/GUIDE.md) — 发信触发
- [campaigns](../campaigns/GUIDE.md) — 产品页回信监听 UI
- [performance](../performance/GUIDE.md) — env 开关与锁说明
- 文档：`agent_prj/docs/kol-operator-gmail-onboarding.md`

## 安全

Token 静态加密存储；勿在 UI 展示 token 或文件路径。
