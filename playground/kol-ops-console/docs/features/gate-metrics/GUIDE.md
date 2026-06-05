# 门禁效果看板

## 功能说明

展示回信草稿 **首轮通过率**、**KOL 候选采纳率**、**初邀回信率**、高频 **驳回标签** 等指标，帮助操作员与运营评估 AI 质量（非开发监控）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/metrics` | `GateMetricsPage.tsx`（门禁指标 + **红人列表**表格） |

## 顶部 KOL 漏斗指标（随「近 N 天」窗口，按 `first_discovered_at` 筛选）

| 指标 | 公式 | 说明 |
|------|------|------|
| KOL候选采纳率 | 初邀草稿数 ÷ 可采纳候选数 | 可采纳 = 发现总数 − **曾触达列表**历史合作红人 |
| 初邀回信率 | 有回信数 ÷ 初邀草稿数 | 初邀草稿后存在 `kol_inbound_reply` |

卡片副文案展示分子/分母（如 `12 / 85 生成初邀草稿`）。

## 红人列表（Agent红人列表模板）

指标页下半区展示 **全部发现过的 KOL**（含仅 `discovered`、未过 shortlist 审批的条目），列与 Excel 模板对齐：

| 列 | 含义 |
|----|------|
| 序号 | 分页序号 |
| ID | Instagram handle，可点进 KOL 详情 |
| IG链接 | 主页 URL |
| 内部曾触达次数 | 在 **曾触达列表.xlsx** 全部 sheet 中按行匹配 handle/邮箱/链接；**每匹配 1 行 +1**（跨 sheet 累加）；未匹配为 0 |
| 初邀已批准 | 是否已生成初邀 Gmail 草稿（`kol_initial_outreach_draft_ready` / `outbound_draft_created`+`outreach` / `offer.outreach_draft_created`） |
| 有回信 | 是否收到红人回信（存在 `kol_inbound_reply` 事件） |
| 目标SPU | 最近关联活动对应产品 SKU（无则空） |
| 粉丝量 / 平均播放 | Nox 或身份事实；无则空 |
| 受众画像 | 「查看画像」按钮，悬停展示与详情页相同的 Nox 受众图表 |
| 邮箱 | 身份邮箱；无则空 |

- 每页 50 条，支持按 ID/邮箱搜索；默认按 **入库时间**（`campaign_candidates.created_at` 最早值）**降序**；点击表头可切换升序。切换 TEST/LIVE 会重置页码。
- 列表数据 **单请求分页加载**（含受众事实），悬停不再额外请求。
- **数据范围**：仅 `campaign_candidates`（Agent 发现候选），**不含**历史红人登记导入（`legacy.collab_imported` / `import_red_kol_history.py`）。
- **曾触达白名单**：不在 `曾触达列表.xlsx` 内的 KOL 内部触达次数恒为 0（含后续新发现的 KOL、新产生的邮件/草稿事件）。Bridge 默认直接读 `~/Documents/曾触达列表.xlsx`（文件变更后自动生效，无需重启）；无该文件时回退 `data/prior_touch_allowlist.json`。部署机可设 `KOL_PRIOR_TOUCH_ALLOWLIST_XLSX` 或运行 `import_prior_touch_allowlist.py` 刷新 JSON。白名单**仅**影响「内部曾触达次数」列，不触发 KOL 发现跳过（发现跳过见 `list-discovery-skip-handles` 的归档 `last_outcome`）。
- **导出 Excel**：工具栏「导出 Excel」→ `GET /admin/kol-registry/export`；列与模板一致（受众画像导出为文字摘要）。

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `GateMetricsPage.tsx`, `KolRegistryTable.tsx`, `NoxAudienceHoverPanel.tsx` |
| BE | `routers/admin.py`, `kol_registry_export.py`（`GET /admin/gate-metrics`, `GET /admin/kol-registry`, `GET /admin/kol-registry/export`） |
| Bridge | `kol-ops-bridge/cal.py` → `list_discovered_kol_registry`, `GET /kol-registry` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/gate-metrics` | 聚合指标（带 `env`, `days`）；含 `kol_funnel` 分子分母 |
| GET | `/kol-registry/funnel`（bridge） | 发现漏斗原始计数与比率 |
| GET | `/admin/kol-registry` | 分页红人列表（`env`, `limit`, `offset`, 可选 `q`） |
| GET | `/admin/kol-registry/export` | 下载 `.xlsx`（当前 `env`/`q` 下**全部**行，非仅本页） |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 驳回标签来源 `rejectTags.ts`
- [auth-settings](../auth-settings/GUIDE.md) — 同 `admin` router

## UX

图表/数字配 **中文说明**（何为「首轮通过」）；可按活动或时间筛选时写清筛选含义。
