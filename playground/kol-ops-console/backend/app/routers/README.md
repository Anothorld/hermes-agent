# Backend Routers — API 与功能模块对照

总架构：[../../../ARCHITECTURE.md](../../../ARCHITECTURE.md) · 功能指引：[../../../docs/README.md](../../../docs/README.md)

| Router 文件 | 功能模块 GUIDE |
|-------------|----------------|
| `auth.py` | [auth-settings](../../../docs/features/auth-settings/GUIDE.md) |
| `google_auth.py`, `internal.py` | [gmail](../../../docs/features/gmail/GUIDE.md) |
| `products.py` | [products](../../../docs/features/products/GUIDE.md) |
| `campaigns.py`, `candidates.py`, `reply_watcher.py` | [campaigns](../../../docs/features/campaigns/GUIDE.md) |
| `kols.py`, `facts.py`, `goals.py`, `relationships.py` | [kols](../../../docs/features/kols/GUIDE.md) |
| `approvals.py` | [approvals](../../../docs/features/approvals/GUIDE.md) |
| `learning.py` | [learning](../../../docs/features/learning/GUIDE.md) |
| `escalations.py` | [escalations](../../../docs/features/escalations/GUIDE.md) |
| `policies.py` | [policies](../../../docs/features/policies/GUIDE.md) |
| `gateway_approvals.py` | [agent-gateway](../../../docs/features/agent-gateway/GUIDE.md) |
| `admin.py` | [gate-metrics](../../../docs/features/gate-metrics/GUIDE.md), [auth-settings](../../../docs/features/auth-settings/GUIDE.md) |
| `events.py` | [live-events](../../../docs/features/live-events/GUIDE.md) |

挂载入口：`../main.py`（`include_router`）。
