# 门禁效果看板

## 功能说明

展示 **KOL 发现统计**（全量计数）、回信 **首轮通过率**、高频 **驳回标签** 等指标，帮助操作员与运营评估 AI 质量（非开发监控）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/metrics` | `GateMetricsPage.tsx`（KOL 发现统计 + 门禁指标 + **红人列表**表格） |

## 时间窗口说明

| 区域 | 窗口 |
|------|------|
| **KOL 发现统计** | **全量**（当前 TEST/LIVE 环境全部发现红人，不受「近 N 天」影响） |
| 审批/升级类指标 | 工具栏「近 7 / 14 / 30 天」 |
| 趋势图 | 与汇总窗口**独立**，按「趋势按天/周/月/年」粒度；KOL 发现为各时段**末累计**，审批/升级为时段内事件 |

## KOL 发现统计（顶部四格）

按 `campaign_candidates` **每个红人取最新一条**记录的状态计数，**不做**曾触达排除、14 天成熟 cohort 等筛选。

| 显示 | 字段 | 状态映射 |
|------|------|----------|
| 全部发现 | `discovered_total` | 去重 identity 总数 |
| 已通过 | `passed_count` | `selected_for_outreach` |
| 待处理 | `pending_count` | `discovered` / `shortlisted` / `needs_review` / `pool_pending_approval` |
| 已否决 | `rejected_count` | `rejected` / `archived` |

辅助：**通过率** = 已通过 ÷ 全部发现。

### 初邀回信（同区第二行，全量无窗口）

| 显示 | 字段 | 说明 |
|------|------|------|
| 初邀回信率 | `initial_outreach_reply_rate` | 有回信 ÷ 有初邀草稿 |
| 有初邀草稿 | `initial_outreach_draft_count` | `kol_initial_outreach_draft_ready` / `outbound_draft_created`+`outreach` / `offer.outreach_draft_created` |
| 有回信 | `initial_outreach_reply_count` | 存在真实 `kol_inbound_reply`（**不含**退信 DSN、Out of Office 等自动回复） |
| 待回信 | `pending_reply_count` | 有初邀草稿、尚无真实回信（含仅收到自动退信/自动回复的） |
| 已排除 | `automated_reply_excluded_count` | 有初邀草稿、仅收到自动退信/自动回复的红人数量 |

自动退信/自动回复识别：`mailer-daemon` / `postmaster` 退信 DSN，以及 Out of Office / automatic reply 等正文或主题特征（见 `inbound_reply/automated.py`）。

不做 14 天成熟 cohort、曾触达排除或「近 N 天」窗口筛选。

## 审批/升级类指标公式

| 指标 | 公式 | 说明 |
|------|------|------|
| 回信首轮通过率 | 无 prior refine 的 approve ÷ 首轮决策数 | 仅 `approval.reply_draft`；先「优化重写」再批准不计入首轮 |
| 平均处理时长 | mean(决定时刻 − opened_at) | audit 须含 `opened_at`（新批准/驳回/结案自动写入） |
| 人工触点 / 有触达活动 | 触达次数之和 ÷ 有触达的活动数 | 含 approve/reject/refine/升级结案/预览草稿 |
| 升级终止率 | terminate 结案 ÷ 全部升级结案 | 近 N 天 |
| LIVE 驳回率 | LIVE reject ÷ LIVE 回信审批 | 非 gateway 事故；TEST 恒为 0 |

> **已移除**：KOL 候选采纳率、初邀回信率（原 14 天成熟 cohort + 曾触达排除逻辑）。Bridge 仍保留 `/kol-registry/funnel` 供脚本/调试，Console 看板不再展示。

## 指标趋势图

**KOL 发现统计**与 **5 张审批/升级指标卡片**内嵌迷你趋势图（柱形），工具栏可切换粒度：

| 粒度 | 默认展示时段数 | 说明 |
|------|----------------|------|
| 按天 | 近 30 天 | KOL：各时段末累计；审批/升级：来自 Console `audit_log` |
| 按周 | 近 12 周 | |
| 按月 | 近 12 月 | |
| 按年 | 近 5 年 | 悬停柱条可看该时段数值 |

KOL 发现趋势序列：`discovered_total`、`passed_count`、`pending_count`、`rejected_count`、`pass_rate`、`initial_outreach_draft_count`、`initial_outreach_reply_count`、`pending_reply_count`、`initial_outreach_reply_rate`（Bridge `aggregate_kol_discovery_summary_trend`，按 `first_discovered_at` 累计）。

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
- **曾触达白名单**：不在 `曾触达列表.xlsx` 内的 KOL 内部触达次数恒为 0（含后续新发现的 KOL、新产生的邮件/草稿事件）。Bridge 默认直接读 `~/Documents/曾触达列表.xlsx`（文件变更后自动生效，无需重启）；无该文件时回退 `data/prior_touch_allowlist.json`。白名单**仅**影响「内部曾触达次数」列，不触发 KOL 发现跳过（发现跳过见 `list-discovery-skip-handles` 的归档 `last_outcome`）。
- **导出 Excel**：工具栏「导出 Excel」→ `GET /admin/kol-registry/export`；列与模板一致（受众画像导出为文字摘要）。

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `GateMetricsPage.tsx`, `MetricTrendSparkline.tsx`, `KolRegistryTable.tsx`, `NoxAudienceHoverPanel.tsx` |
| BE | `gate_metrics_audit.py`, `gate_metrics_trends.py`, `routers/admin.py`, `routers/approvals.py`, `routers/escalations.py` |
| Bridge | `cal.py` → `aggregate_kol_discovery_summary` / `aggregate_kol_discovery_summary_trend`；`aggregate_kol_registry_funnel`（遗留 API） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/gate-metrics` | 聚合指标（`env`, `days`）；含 `kol_discovery_summary`, `audit_meta` |
| GET | `/admin/gate-metrics/trends` | 审批/升级 + KOL 发现趋势序列（`env`, `bucket`, 可选 `periods`） |
| GET | `/kol-registry/summary`（bridge） | 全量发现计数（无筛选） |
| GET | `/kol-registry/summary/trend`（bridge） | KOL 发现累计趋势序列 |
| GET | `/kol-registry/funnel`（bridge） | 遗留漏斗（Console 看板不再使用） |
| GET | `/admin/kol-registry` | 分页红人列表 |
| GET | `/admin/kol-registry/export` | 下载 `.xlsx` |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 驳回标签来源 `rejectTags.ts`
- [auth-settings](../auth-settings/GUIDE.md) — 同 `admin` router

## UX

KOL 发现统计用大号数字四格 + 初邀回信三格展示，并附累计趋势 sparkline；审批类指标仍配中文说明与趋势图。
