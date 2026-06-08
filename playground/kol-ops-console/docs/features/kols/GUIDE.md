# KOL（身份 / 看板 / 详情）

## 功能说明

以 **identity_id** 为中心的 KOL 运营：8 阶段看板、详情页（事实、目标、邮件、发现、社交、Nox）、关系图、归档合作。状态权威在 Bridge；Console 聚合展示并触发 Agent。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/kols` | `KolKanbanPage.tsx`（默认首页 `/` 重定向至此；**仅 shortlist 已批准** KOL；空看板时提示检查 LIVE env） |
| `/kols/archive` | `KolArchivePage.tsx` |
| `/kols/:id` | `KolDetailPage.tsx`（`KolProfileDashboard` 摘要 + `NoxDiligencePanel` 统一 Nox 数据） |
| `/kols/:id/relationship` | `KolRelationshipPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | 上表 pages；`KolProfileDashboard`, `KolProfilePreviewLink`, `NoxDiligencePanel`, `NoxInsightsSections`, `LaneFilterBar`, `GoalProgressBar`, `FactsEditor`, `CommunicationHistoryPanel` |
| BE | `routers/kols.py`, `facts.py`, `goals.py`, `relationships.py` |
| 标签 | `components/factKeyLabel.ts`, `constants/domainLabels.ts` |

## 主要 API（节选）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/kols`, `/kols/{id}` | 列表/详情（含跨活动 `prior_outreach_touch`：曾触达 / 近期已触达 + 多久前） |
| GET | Bridge `/identities/outreach-touch` | 短名单批量触达标签（Console 内部） |
| GET/PATCH | `/facts`, `/facts/multi` | CAL 事实 |
| GET | `/identities/{id}/goals` | 目标/泳道进度 |
| GET | `/kols/{id}/communication-history` | 绑定邮箱线程 |
| POST | `/kols/{id}/discover-email` | Nox Gate B → `kol-email-discovery`（Tier 1 WebSearch/WebFetch，Tier 2 仅 `browser_*`；**禁止** `veedcrawl_*`、`delegate_task`、`mcp_chrome_devtools_*`；guard 在 `kol-email-discover:*` 会话硬拦；gateway instructions 含 `terminal_safety` + no-hang） |
| POST | `/kols/{id}/discover-social-links` | `kol-social-link-discovery`（同上 CLI/terminal 契约；browser 用 Tier 2 no-hang 纪律） |
| POST | `/kols/{id}/email`, `/kols/{id}/nox-contacts`, `/kols/{id}/nox/*` | 手动邮箱 / 仅 Nox Gate B / 尽调与监测 |

## 全网搜索邮箱（kol-email-discovery）— Tier 2 无挂起纪律（POVISON 686 修复）

686 现象：浏览器**只开了一个空 `about:blank` 标签页，从未导航到任何网页**，run 随后挂住。根因不是页面加载超时（浏览器 CLI 本身有 60s 硬超时会 kill），而是：被强停的 run 不会执行 `cleanup_browser`，其 pool 标签页**泄漏**为孤儿 `about:blank`，在共享 debug Chrome 里越积越多、拖慢后续 CDP attach；叠加并行 email-discover 批量把 gateway 槽位/LLM 打满。

修复：
- **tab-pool 插件**（`plugins/local-chrome-tab-pool/internal/tab_pool.py`）：`acquire()` 前调用 `reap_orphan_blank_tabs()`，只关闭**未被 pool 跟踪且 url 为 `about:blank`** 的 page target——真实页面与在用标签页不受影响。
- **技能 + brief 纪律**：Tier 2 一页一次、单次尝试，导航/快照报错或超时即记入 `tried` 继续，绝不重试同一 URL；用尽 8 页预算即返回 miss；Chrome 无法启动则 miss `browser_unavailable`。**绝不让 run 挂死**。
- **并发**：一次只跑一个 `kol-email-discovery`，不要为多个身份并行浏览器发现（会饱和 gateway 槽位与共享 Chrome）。
- **CLI 错误**：bridge CLI 失败路径在 **stdout** 输出 JSON；空 terminal + exit 2 应读 stdout 的 `error`/`hint`，禁止转 `execute_code`。
- **工具误选（701）**：模型曾用 `delegate_task` 派子代理、子代理空参调用 `veedcrawl_*`（`bad_request`，未到 API）。`kol-bridge-agent-guard` 现对 `kol-email-discover:*` 拦截 `veedcrawl_*` 与 `delegate_task`；技能与 gateway instructions 同步写明 Tier 1/2 正路径。

## 列表性能（看板 / 审批 / 详情）

| 场景 | 优化要点 |
|------|----------|
| 看板 `GET /campaigns/{id}/lanes` | Bridge 批量读 CAL（**仅 shortlist 已批准**的候选人 + 泳道/事实 + **按 campaign 过滤**的待审批与升级，不扫全 env）；`discovered` / `shortlisted` 只在产品页 Shortlist review 出现，不进看板；**勿用 lanes 行数当 shortlist / picker 候选数**（见 [campaigns §数据源边界](../campaigns/GUIDE.md)）；FE 卡片入列取 **pipeline 最靠前的进行中 goal**（避免「全部」列时卡片落到右侧履约列）；Console ~8s 缓存；`useCampaignQuerySync`：深链写入 store，顶部 campaign 切换会更新 URL 并重新拉 lanes |
| 待审批 `GET /approvals` | Bridge JOIN `kol_identity` 带 `handle`；支持 `identity_id` + `campaign_id` 过滤 |
| 升级 `GET /escalations` | 支持同上过滤；列表 JOIN `handle` |
| KOL 详情 | 审批/升级请求带 `identity_id` + `campaign_id`，不再拉全表 |
| 产品短名单 / 多活动 | Nox 字段 `POST /facts/batch-subset`；产品页每活动只调一次 `lanes`；身份卡片 `POST /identities/briefs` |
| 候选人池 | `list_candidate_handles` + relationship JOIN，一次返回 handle/合作次数 |

## 关联模块

- [campaigns](../campaigns/GUIDE.md) — `/campaigns/{id}/lanes` 驱动看板
- [approvals](../approvals/GUIDE.md) — `approval.*` 事实
- [escalations](../escalations/GUIDE.md) — 身份级升级
- [gmail](../gmail/GUIDE.md) — 邮箱绑定与历史
- [nox](../nox/GUIDE.md) — 尽调/联系人/监控

## KOL 主页快速预览

| 位置 | 行为 |
|------|------|
| KOL 详情 `KolProfileDashboard` | 标题旁 **预览主页**：悬停小窗预览，点击新标签页打开 |
| 详情顶栏 `KolProfileDashboard` / `KolSocialQuickLinks` | 名称区下方各平台 chip，支持悬停预览 |
| 产品页 Shortlist review | 与详情相同的 **快速跳转**（读 identity 全部 `*_profile_url` fact，与 `/facts` 一致）+ 悬停预览 |

组件：`frontend/src/components/KolProfilePreviewLink.tsx`；URL 解析：`frontend/src/lib/kolProfileUrl.ts`、Console `backend/app/kol_profile_url.py`。

**Instagram 无法 iframe 内嵌**（X-Frame-Options）。曲线方案：

| 层 | 行为 |
|----|------|
| `GET /link-preview?url=…&identity_id=` | Meta 爬虫 UA 拉 OG；有 `identity_id` 时读写 CAL 缓存 fact（7 天 TTL） |
| Shortlist `GET …/shortlist` | 每行带 `link_preview`；无 `identity_id` 的候选按 handle+platform 推断 URL 并优先批量拉 OG（每请求最多 12 条） |
| CAL facts | `identity.profile_og_*`（头像 URL、标题、简介、抓取时间、来源 URL） |

悬停卡片叠加 Nox/粉丝等 `preview_facts`；二次打开优先 CAL，减少重复抓取。

## Lane / Goal

泳道：`commerce`, `fulfillment`, `publish`, `meta`（类型见 `api.ts` `Lane`）。
