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
| POST | `/kols/{id}/discover`, `/email/*`, `/nox/*` | 发现/邮件/Nox |

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
