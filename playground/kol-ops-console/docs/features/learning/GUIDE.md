# 自主学习

## 功能说明

从操作员 **编辑/驳回** 信号中学习：批次进度、生成 style 提案、策略反哺（`reply_strategy` → skill reference）。Bridge 执行学习任务；Console 提供可视化与手动触发。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/learning` | `LearningPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `LearningPage.tsx`, `LearningManualTriggerSection.tsx`, `LearningWorkflowStepper.tsx`, `StrategyPromotionPanel.tsx`, `OutcomePromotionPanel.tsx` |
| BE | `routers/learning.py` |
| 外链 | `agent_prj/docs/kol-learning-tier1-implementation.md` |

## 主要 API

| 方法 | 路径 | Bridge 代理 |
|------|------|-------------|
| GET | `/learning/overview` | overview（含 `edit_distance_trend` 收敛度量） |
| GET | `/learning/edit-distance-trend` | 编辑幅度趋势（收敛度量，只读；含批准标注） |
| GET | `/learning/preview-edit-batch` | 下一批将蒸馏的编辑样本预览（只读）；`edited_available` 为**条数**（与 overview 一致），未达阈值时 `reason=below_style_learning_batch_threshold` |
| POST | `/learning/policy-merge-preview` | 批准前 current vs merged policy 预览 |
| POST | `/learning/run-jobs` | scheduled jobs（LIVE） |
| GET | `/learning/job-runs`, `edit-events`, `reject-events` | 同名 |
| POST | `/learning/propose-edit-policy` | apply-edit-policy |
| POST | `/learning/backfill-edit-learning` | backfill sent drafts missing `draft_edit_learning` |
| POST | `/learning/promote-strategy` | promote-strategy |
| GET | `/learning/policies/{scope}` | policies |

## 批次语义（重要，避免误解）

- 「生成学习提案」的阈值 `KOL_STYLE_LEARNING_BATCH_SIZE`（默认 **5**）指**累计 N 条 `draft_edit_learning` 事件**（操作员在 Gmail 改过 AI 草稿后发送，`was_edited=true`），**不是「N 封任意邮件」也不是「固定 N 个 KOL」**。
- **编辑批次进度**显示全库可蒸馏合计；**按范围分表**（`edit_stats_by_scope`）分别统计 `company_style` 与各 `user_style` 操作员。已生成、待审批的提案会占用一批样本（`edited_queued_in_pending`），批准后才从池子消费。
- 批准合并默认 `KOL_STYLE_LEARNING_MERGE_MODE=replace_section`；审批卡可展开「合并效果」并显示 **将新增 / 将替换 / 将追加**（`merge_effect`）。
- **每条事件 = 一次「Agent 草稿→操作员终稿」的 diff**，并附带该 KOL+campaign 的会话时间线（最多 30 条）+ 当前 facts，作为 1 个 LLM 样本。
- 10 条事件可能来自 10 个不同 KOL，也可能少于 10 个（同一 KOL 多次编辑各占一条）。提案卡片展示 `sample_identity_count`（涉及多少位 KOL）。

## 收敛度量（是否「越来越准」）

- `edit_distance ∈ [0,1]`（0=照发，1=完全重写）已对每封产出并入库。
- `GET /learning/edit-distance-trend`（overview 内置近 90 天按周聚合）给出 `avg_edit_distance`、`was_edited_rate`、按时间桶趋势、`style_approval_markers`（趋势图绿色竖线）与 `channel_trends`（编辑/驳回/复盘三通道）。
- 护栏：有批准记录时 `convergence_alert.guard_basis=after_last_approval`（最近一次批准后 vs 批准前平均幅度）；否则回退 `recent_vs_prior`。
- 滑动窗默认 `KOL_LEARNING_WINDOW_DAYS=90`（`0` 关闭）。

## 合作复盘学习（结局级，新增）

不同于过程级（编辑/驳回）学习，这是**结果级**：合作达成/失败后做根因分析，沉淀到后续流程。

| 层 | 触发 | 落点 |
|----|------|------|
| Tier1 单案复盘（阈值=1） | 归档即触发（job `analyze_collab_outcome`） | `collab_outcome_learning` 事件（outcome_class / root_cause_tags / what_worked/failed / price_summary / forward_guidance） |
| Tier2 模式综合（分层阈值） | **按 goal/segment**：该段内 ≥5 次复盘或 ≥3 次失败先到（job `apply_outcome_policy`） | pending `approval.outcome_learning_proposal` → 批准合并入 `outcome_strategy` policy |

- 注入：`outcome_strategy` 按 goal 切片进 `learning_hints`（advisory，HARD 规则优先）。
- Console：Learning 页「合作复盘学习」卡片（复盘数/失败数/触发进度）；批准在「待审批 → 合作复盘」Tab。
- 实现：`learning_outcome.py`（Bridge）。

## 关联模块

- [approvals](../approvals/GUIDE.md) — 批准学习提案 / 合作复盘提案
- [policies](../policies/GUIDE.md) — policy 预览（含 `outcome_strategy`）
- [kols](../kols/GUIDE.md) — 归档合作触发复盘
- 升格后需运行 `playground/learning/sync_skills.py`（操作员文档见根 README）
- **回信策略升格**：`StrategyPromotionPanel`（`reply_strategy` → `references/learned/<goal>.md`）
- **合作复盘升格**：`OutcomePromotionPanel`（`outcome_strategy` → `references/learned/<goal>.outcome.md`）

## 闭环步骤（5 步）

见 `LearningWorkflowStepper.tsx`：信号积累 → 提案 → 待审批 → 策略反哺 →（可选）sync skills

## 手动操作（页内 §2）

| 操作 | 含义 | 环境 |
|------|------|------|
| **运行套件** | 批量 cron 任务（采集 / 蒸馏 / 定价等，依套件） | 固定 LIVE；可先「仅预览」 |
| **生成学习提案** | 仅 `apply_edit_policy`：达批次阈值 → pending `approval.style_learning_proposal` | 页顶 TEST/LIVE |

「蒸馏 / 夜间」套件非预览执行时，也会顺带生成学习提案（与右侧按钮同逻辑）。组件：`LearningManualTriggerSection.tsx`；套件说明文案：`SUITE_OPERATOR_HINTS`（`domainLabels.ts`）。

**502 / 超时：** 生成学习提案含 LLM 蒸馏，Bridge 侧常需 **1–3 分钟**。Console 默认 Bridge 读超时 60s 会误报 502；`propose-edit-policy` / `run-jobs` 已用 `KOC_BRIDGE_LEARNING_TIMEOUT_SEC`（默认 300s）。操作员见「生成中」提示，勿连点。

**必须 LLM：** 编辑学习提案不再静默回退「规则聚合」。LLM 失败返回 503 并说明原因。Bridge 优先用 `~/.hermes` 解析出的 HTTP 端点（无需 Gateway agent 会话）；standalone `serve.py` 启动时会加载 `~/.hermes/.env` 与 `plugins/kol-ops-bridge/.env`。若 HTTPS 报证书错误，在 bridge venv 安装 `certifi`；若要走 `call_llm`，还需安装 `openai`。
