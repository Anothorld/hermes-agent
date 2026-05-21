# KOL Ops Console — Frontend ↔ Backend ↔ Bridge 对齐审计

> 生成时间：2026-05-21  
> 基线：commit `140c3cf91`（已合并 web 清理：导航瘦身 + bridge stubs + 0.0.0.0 + Phase A 启动 UI 下线）  
> 工具：read-only Explore agent；未改任何文件  
> 范围：  
> - 前端：`playground/kol-ops-console/frontend/src/pages/*.tsx`、`App.tsx`  
> - Console 后端：`playground/kol-ops-console/backend/app/routers/*.py`  
> - Bridge plugin：`hermes-agent/plugins/kol-ops-bridge/plugin_api.py`、`bridge_client.py`

本报告供 operator 勾选后再批量落地。**未经勾选不执行任何删除/重构。**

---

## A — 前端 Page 清单

| Page | Route | 在导航 | 调用端点 | 后端状态 |
|---|---|---|---|---|
| LoginPage | `/login` | 否（公开） | `POST /auth/login` | ✓ |
| ProductListPage | `/products` | ✓ | `GET /products/summary`、`POST /products` | ✓ |
| ProductDetailPage | `/products/:sku` | 否 | `GET /campaigns/{id}/shortlist`、`POST /campaigns/{id}/approve-shortlist` | ✓ |
| KolKanbanPage | `/kols` | ✓ | `GET /campaigns/{id}/lanes`、`GET /approvals`、`GET /escalations` | ✓ |
| KolDetailPage | `/kols/:id` | 否 | `GET /kols/{id}`、`GET /identities/{id}/goals`、`GET /escalations`、`GET /approvals` | ✓ |
| KolRelationshipPage | `/kols/:id/relationship` | 否 | `GET /identities/{id}/relationship`、`.../reusable-facts` | ✓ |
| CampaignWizardPage | `/campaigns/new` | 否 | `PUT /campaigns/{id}` | ✓ |
| CampaignCandidatesPage | `/campaigns/:id/candidates` | 否 | `GET/POST /campaigns/{id}/candidates`、`POST .../resolve-relationships`、`POST .../select`、`POST /escalations` | ✓ |
| **DraftQueuePage** | `/drafts` | 否 | `GET /drafts/pending` | ⚠️ 桩返回 `[]` |
| **BudgetBoardPage** | `/budget` | 否 | — | — 占位页 |
| **ReplyMonitorPage** | `/replies` | 否 | `GET /events/recent`、`GET /escalations/open`、`POST /escalations/{id}/next-action` | ❌ 多处 missing |
| **FunnelReportPage** | `/reports` | 否 | — | — 占位页 |
| ApprovalsPage | `/approvals` | ✓ | `GET /approvals`、`POST .../approve`、`POST .../reject` | ✓ |
| EscalationConsolePage | `/escalations` (+ `/:id`) | ✓ | `GET /escalations`、`POST /escalations`、`PATCH /escalations/{id}` | ✓ |
| PolicyEditorPage | `/policies` | ✓ | `GET/PUT /policies/{scope}`、`.../history` | ✓ |
| SettingsPage | `/settings` | ✓ | `GET /auth/me`、`POST /auth/users`、`POST /admin/wipe-test` | ✓ |

---

## B — Console 后端 Router 清单

| Router | 前缀 | 端点 | 调用方 | Bridge 接线 |
|---|---|---|---|---|
| `auth.py` | `/auth` | `POST /login`、`GET /me`、`POST /users` | Login/Settings/Policy | ✓ 本地 DB |
| `products.py` | `/products` | `GET`、`/summary`、`POST`、`/{sku}`、`/{sku}/campaigns` | ProductList/Detail | ✓ |
| `kols.py` | `/kols` | `GET`、`/{id}`、`/{id}/timeline`、`POST /{id}/notes` | 多页 | ⚠️ timeline 桩 |
| `campaigns.py` | `/campaigns` | `POST /{id}/start`、`/close`、`GET /shortlist`、`POST /approve-shortlist`、`POST /replies/inbound`、`GET /lanes` | ProductDetail/Wizard | ⚠️ start/replies 桩 |
| `candidates.py` | `/campaigns/{id}/candidates` | `GET`、`POST`、`/resolve-relationships`、`/select` | CampaignCandidates | ✓ |
| `drafts.py` | `/drafts` | `GET /pending`、`GET /{id}` | DraftQueue | ⚠️ 桩 |
| `escalations.py` | `/escalations` | `GET`、`POST`、`PATCH /{id}` | 多页 | ✓ |
| `approvals.py` | `/approvals` | `GET`、`POST /{path}/approve`、`/reject` | Approvals/Kanban | ✓ |
| `policies.py` | `/policies` | `GET/PUT /{scope}`、`/history`、`/escalation_rules/parsed` | Policy | ✓ |
| `goals.py` | `/identities` | `GET /{id}/goals`、`/dispatch-context` | KolDetail | ✓ |
| `facts.py` | `/facts` | `GET/POST /{id}`、`/multi` | KolDetail | ✓ |
| `relationships.py` | `/identities` | `GET /{id}/relationship`、`/reusable-facts`、`POST /archive` | KolRelationship | ✓ |
| **`events.py`** | — | `GET /events/recent`、`GET /escalations/open`、`POST /escalations/{id}/next-action`、`WS /ws` | ReplyMonitor | ❌ **3 个 bridge 方法缺失** |
| **`content.py`** | `/content` | `POST /verdict` | **无** | ❌ `push_content_verdict` 不存在 |
| **`logistics.py`** | `/logistics` | `POST /update` | **无** | ❌ `push_logistics_update` 不存在 |
| **`contract.py`** | `/contract` | `POST /update` | **无** | ❌ 桩 501 |
| `admin.py` | `/admin` | `POST /wipe-test`、`GET /audit` | Settings | ✓ |

---

## C — Bridge 路由清单（`/api/plugins/kol-ops-bridge/`）

31 个端点，bridge_client.py 包装 30 个。  
**唯一未包装**：`POST /admin/check-stuck-goals`（admin-only，console 暂无需求）。

健全部分略——所有 identity / campaign / candidates / facts / goals / approvals / escalations / policies / archive 路径都有正确双向接线。

---

## D — 问题分类

### D1 · 孤儿前端页（路由挂着但导航不可达）

| Page | Route | 建议 |
|---|---|---|
| DraftQueuePage | `/drafts` | Phase A 已废弃 drafts 持久化 → **删页面 + 删路由 + 删 `drafts.py` router + 删 bridge_client 的 `list_pending_drafts/get_draft`** |
| BudgetBoardPage | `/budget` | 纯占位 "Coming next" → **删页面 + 删路由** |
| ReplyMonitorPage | `/replies` | 见 D2 |
| FunnelReportPage | `/reports` | 纯占位 → **删页面 + 删路由** |
| ProductDetail / KolDetail / KolRelationship / CampaignCandidates / CampaignWizard | 各种 detail | **保留**，从父页面跳转可达 |

### D2 · 死 UX（UI 存在但后端必坏）

| Page | 问题 | 建议 |
|---|---|---|
| ReplyMonitorPage | `POST /escalations/{id}/next-action` → bridge_client 没有 `choose_escalation_next_action`，会 500；侧边栏 `GET /escalations/open` → bridge_client 没有 `list_open_escalations`，会 500 | **A. 直接删页面 + 删 events router 的 escalation 部分**（推荐，escalation 已有专门的 EscalationConsolePage 覆盖）<br>**B. 补 bridge_client 方法并保留页面** |
| DraftQueuePage | 同 D1 | 见 D1 |

### D3 · 死后端 router（无前端调用 + bridge 方法缺失）

| Router | 端点 | 建议 |
|---|---|---|
| `content.py` | `POST /content/verdict` | **删 router**（无 UI，bridge 也没接） |
| `logistics.py` | `POST /logistics/update` | **删 router** |
| `contract.py` | `POST /contract/update` | **删 router**（桩 501） |
| `admin.py` `GET /audit` | 仅本地审计读取 | **保留**（ops 调试用） |

### D4 · bridge_client 桩方法

| 方法 | 行为 | 真因 | 处置 |
|---|---|---|---|
| `recent_events` | `[]` | bridge 缺 `/events` 读端点 | Phase B 实现后再补 |
| `list_identities` | `[]` | bridge 缺 `GET /identities` 列表端点 | 当前无前端调用 → **删** |
| `get_timeline` | `[]` | bridge 缺 `/timeline` | 仅 `/kols/{id}/timeline` 调用 → 看是否前端有真展示需求 |
| `list_pending_drafts` / `get_draft` | `[]` / 404 | Phase A 退役 | **随 drafts 一并删** |
| `inject_inbound_reply` | 501 | bridge 没此端点 | 无调用 → **删** |
| `start_campaign` | 501 | 设计上走 gateway | **Phase B 重新实现**（见下节） |
| `push_contract_update` | 501 | bridge 没此端点 | 随 contract router 一并 **删** |

### D5 · 未包装 bridge 端点

| 端点 | 是否需要 console 入口 |
|---|---|
| `POST /admin/check-stuck-goals` | 低优先；当前用 CLI 即可 |

### D6 · 其他

- 前端组件 FactsEditor / GoalProgressBar / RepeatKolBadge / useLiveEvents 钩子全部有真实数据通路（虽然 events 是空），**无死代码**
- WS `/ws` 后端实现但 events 数据源为空，behavior 退化为空推送——能用

---

## E — Phase B 启动链路（待批准实现）

按本次确认，**已获你明确批准**修改 `gateway/` 与 `agent/` 把"console 启动 campaign"链路打通。下一步先扫描 gateway/agent 现有 launch 入口（不在本审计内），单独提出实施计划再动手。

最小可行链路（待证实/落地）：

```
console UI "Start campaign"
  → console backend POST /campaigns/{id}/start (已存在)
    → bridge_client.start_campaign(...)  # 目前抛 501
      → 改为：HTTP POST gateway:<port>/orchestrator/launch  # 待证实端点
        → gateway 启动 agent run (kol-discovery-to-outreach-router)
          → 返回 run_id
```

或：bridge 直接负责"seed config + 标记 running"，agent run 由 operator 手动从 CLI/chat 触发（薄实现，前次推荐）。

---

## F — 建议执行顺序（待勾选）

**Wave 1（纯删，0 风险）**
- [ ] 删 `BudgetBoardPage.tsx`、`FunnelReportPage.tsx` + App.tsx 路由
- [ ] 删 backend routers `content.py`、`logistics.py`、`contract.py` + main.py include_router 行
- [ ] 删 bridge_client 桩方法 `inject_inbound_reply`、`push_contract_update`、`list_identities`

**Wave 2（drafts 完整下线）**
- [ ] 删 `DraftQueuePage.tsx` + 路由
- [ ] 删 backend `drafts.py` router + main.py 引用
- [ ] 删 bridge_client `list_pending_drafts`、`get_draft`
- [ ] campaigns router 的 `POST /replies/inbound` 同步处理（删 or 留）

**Wave 3（ReplyMonitor 决断）**
- [ ] 路径 A：删 ReplyMonitorPage + events router 的 escalation 块 + bridge_client `recent_events`
- [ ] 路径 B：补 bridge_client `list_open_escalations` + `choose_escalation_next_action` 保留页面

**Wave 4（Phase B 启动链路）**
- [ ] 调研 gateway/agent 现有 launch 接口
- [ ] 出实施方案 → 编码 → 测试 → 接 console UI

---

## G — 不动项（明确保留）

- 所有 in-nav 页：Products / KOLs / Escalations / Approvals / Policies / Settings
- 所有 detail 页：从父页跳转可达
- `admin/audit` 端点（ops 用）
- `POST /admin/check-stuck-goals`（CLI 已能调用）
