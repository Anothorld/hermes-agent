# 门禁效果看板

## 功能说明

展示回信 **首轮通过率**、**KOL 候选采纳率**、**初邀回信率**、高频 **驳回标签** 等指标，帮助操作员与运营评估 AI 质量（非开发监控）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/metrics` | `GateMetricsPage.tsx`（门禁指标 + **红人列表**表格） |

## 时间窗口说明

| 区域 | 窗口 |
|------|------|
| 审批/升级类指标 | 工具栏「近 7 / 14 / 30 天」 |
| KOL 采纳/回信率 | 自动 **至少 30 天** cohort，且仅统计 **已满 14 天** 的成熟样本 |
| 趋势图 | 与汇总窗口**独立**，按「趋势按天/周/月/年」粒度 |

## 顶部指标公式

| 指标 | 公式 | 说明 |
|------|------|------|
| 回信首轮通过率 | 无 prior refine 的 approve ÷ 首轮决策数 | 仅 `approval.reply_draft`；先「优化重写」再批准不计入首轮 |
| 平均处理时长 | mean(决定时刻 − opened_at) | audit 须含 `opened_at`（新批准/驳回/结案自动写入） |
| 重复升级率 | 子升级打开 ÷ 全部升级打开 | 近 N 天，与趋势图定义一致 |
| 人工触点 / 有触达活动 | 触达次数之和 ÷ 有触达的活动数 | 含 approve/reject/refine/升级结案/预览草稿 |
| 升级终止率 | terminate 结案 ÷ 全部升级结案 | 近 N 天 |
| LIVE 驳回率 | LIVE reject ÷ LIVE 回信审批 | 非 gateway 事故；TEST 恒为 0 |
| KOL候选采纳率 | 14 天内出初邀 ÷ 发现满 14 天且落在 cohort 窗口的可采纳候选 | cohort 窗口 = max(所选天数, 30) ～ 14 天前 |
| 初邀回信率 | 14 天内回信 ÷ 初邀满 14 天且落在 cohort 窗口的草稿 | 锚点为 `first_draft_at` |

### KOL 漏斗辅助计数

| 字段 | 含义 |
|------|------|
| `mature_adopted_within_window_count` / `mature_eligible_total` | 采纳率分子/分母 |
| `pending_mature_backlog_count` | 发现已满 14 天、仍无初邀草稿 |
| `pending_immature_count` | 发现未满 14 天、尚无草稿 |
| `mature_replied_within_window_count` / `mature_draft_total` | 回信率分子/分母 |
| `pending_draft_mature_no_reply_count` | 初邀已满 14 天、14 天内仍无回信 |
| `pending_draft_immature_count` | 初邀未满 14 天、尚无回信 |

## 指标趋势图

顶部 **8 张指标卡片**均内嵌迷你趋势图（柱形），工具栏可切换粒度：

| 粒度 | 默认展示时段数 | 说明 |
|------|----------------|------|
| 按天 | 近 30 天 | 审批/升级等来自 Console `audit_log` |
| 按周 | 近 12 周 | KOL 漏斗按成熟 cohort 规则分桶 |
| 按月 | 近 12 月 | 重复升级率 = 子升级 ÷ 全部升级打开 |
| 按年 | 近 5 年 | 悬停柱条可看该时段数值 |

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
| FE | `GateMetricsPage.tsx`, `MetricTrendSparkline.tsx`, `KolRegistryTable.tsx`, `NoxAudienceHoverPanel.tsx` |
| BE | `gate_metrics_audit.py`, `gate_metrics_trends.py`, `routers/admin.py`, `routers/approvals.py`, `routers/escalations.py` |
| Bridge | `cal.py` → funnel / escalation window aggregates |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/gate-metrics` | 聚合指标（`env`, `days`）；含 `kol_funnel`, `audit_meta` |
| GET | `/admin/gate-metrics/trends` | 趋势序列（`env`, `bucket`, 可选 `periods`） |
| GET | `/kol-registry/funnel`（bridge） | 发现漏斗原始计数与比率 |
| GET | `/kol-registry/funnel/trend`（bridge） | KOL 采纳/回信率分桶序列 |
| GET | `/escalations/re-escalation-window`（bridge） | 近 N 天重复升级率 |
| GET | `/escalations/re-escalation-trend`（bridge） | 重复升级率分桶序列 |
| GET | `/admin/kol-registry` | 分页红人列表 |
| GET | `/admin/kol-registry/export` | 下载 `.xlsx` |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 驳回标签来源 `rejectTags.ts`
- [auth-settings](../auth-settings/GUIDE.md) — 同 `admin` router

## UX

图表/数字配 **中文说明**；KOL 指标注明成熟 cohort 与积压计数，避免误读「还没审/还没回」为质量差。
