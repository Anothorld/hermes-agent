# 自主学习

## 功能说明

从操作员 **编辑/驳回/合作结局/发现决策** 信号中学习：批次进度、生成 style 提案、策略反哺（`reply_strategy` → skill reference）、KOL 发现评判标准沉淀。Bridge 执行学习任务；Console 提供可视化与手动触发。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/learning` | `LearningPage.tsx` |

## 自主调度（cron）

除人工审批外，采集与蒸馏可由系统 cron 自动执行（LIVE only）。安装：

```bash
playground/learning/install_learning_cron.sh
```

- **每 15 分钟** — `capture`（Gmail 编辑对齐、回填、合作复盘 Tier1）
- **每天 03:20** — `nightly`（蒸馏提案、发现学习、定价、审计；**提案仍须待审批**）

日志：`~/.hermes/logs/learning/{capture,nightly}.log`。前提：Bridge HTTP 可达。详见 `agent_prj/playground/learning/CRON.md`。

手动「运行套件」与 cron 调用同一 Bridge API，用于补跑或预览。

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
| POST | `/learning/sanitize-policy-metadata` | 清理存量 policy 中的 Context notes / 蒸馏标题（维护） |
| POST | `/learning/run-jobs` | scheduled jobs（LIVE） |
| GET | `/learning/job-runs`, `edit-events`, `reject-events` | 同名 |
| POST | `/learning/propose-edit-policy` | apply-edit-policy |
| POST | `/learning/backfill-edit-learning` | backfill sent drafts missing `draft_edit_learning` |
| POST | `/learning/promote-strategy` | promote-strategy |
| GET | `/learning/policies/{scope}` | policies（含动态 `discovery_criteria:*`） |
| GET | `/learning/discovery-tags` | 发现决策标签词表（`?action=approve\|remove\|transfer`、`?status=proposed` 看挖掘提案） |
| POST | `/learning/discovery-tags/decide` | 批准/拒绝挖掘出的新标签 |
| GET | `/learning/discovery-feedback-requirements` | 该 SPU 样本数 + 评论是否必填（早期必填） |
| GET | `/learning/shortlist-decision-events` | 发现决策学习事件（可按 sku/category/action 过滤） |
| GET | `/learning/pending-discovery-proposals` | 待审批的发现标准提案 |
| GET | `/learning/discovery-criteria` | 某 SKU 已学到的 SPU + 品类评判标准 |
| GET | `/learning/failed-shortlist-captures` | 采集失败待补录 audit 列表（只读） |
| POST | `/learning/replay-shortlist-capture` | 一键补录（重放 audit `replay_body` → Bridge） |
| GET/PUT | `/learning/product-categories[/{sku}]` | SKU→品类映射（人工修正优先于 LLM） |

## 批次语义（重要，避免误解）

- 「生成学习提案」的阈值 `KOL_STYLE_LEARNING_BATCH_SIZE`（默认 **5**）指**累计 N 条 `draft_edit_learning` 事件**（操作员在 Gmail 改过 AI 草稿后发送，`was_edited=true`），**不是「N 封任意邮件」也不是「固定 N 个 KOL」**。
- **待蒸馏样本积压**（学习页 §1）显示窗口内尚未消费的 `draft_edit_learning` **总条数**（可远大于单批阈值，例如积压 175 条但单份提案仍只取 5 条）。**待审批页**卡片上的「编辑样本 N 条」才是该提案实际规模（`sample_count` / `source_event_ids`）。
- Gmail **sent-reconcile** 定时对齐会为每封已发终稿写入学习样本；2026-06-10 曾因未按 `sent_message_id` 去重导致同一封邮件被重复计数（已修复：`gmail_reconcile.has_draft_edit_learning_for_sent_message`）。修复前写入 CAL 的重复行**仍留在库里**，但 **API 读取**（`learning_store.list_learning_events`、编辑趋势）会按 `sent_message_id` 折叠为一条，学习页「编辑信号」不再重复展示同一封已发邮件。
- **编辑信号**（学习页 § 编辑信号）展示的是已入库的 `draft_edit_learning` 历史记录，**不是**当前待审批草稿。同一 KOL+活动 若既有 pending 回信又有历史已发编辑，容易误以为「未发送也算样本」——以事件时间戳 / `sent_message_id` 为准。
- **发送失败（退信 DSN）**不入学习样本、不计入统计：采集时 `is_bounce_body` + 线程内退信检测会跳过 `draft_edit_learning`；读取时 `learning_store` 会剔除退信正文行，并排除同一 KOL+活动 下所有编辑样本（含后续正文正确的行）。历史 CAL 行仍保留，但 API/UI/蒸馏批次不再展示或计数。
- **按范围分表**（`edit_stats_by_scope`）分别统计 `company_style` 与各 `user_style` 操作员。已生成、待审批的提案会占用一批样本（`edited_queued_in_pending`），**仅批准**后才从池子消费；驳回后样本回到队列。
- 批准合并默认 `KOL_STYLE_LEARNING_MERGE_MODE=llm_compress`（**LLM 智能合并**；失败回退 patch）；预览用 patch 近似（`preview_note`）。`### Context notes` **不会写入新批准**；**08:04 之前已写入的存量**需一次性 `POST /learning/sanitize-policy-metadata`（scope=`reply_strategy`, env=LIVE）清理。无规则段跳过（`merge_skipped`）。
- **每条事件 = 一次「Agent 草稿→操作员终稿」的 diff**，并附带该 KOL+campaign 的会话时间线（最多 30 条）+ 当前 facts，作为 1 个 LLM 样本。
- 若 LLM 蒸馏后 **风格与策略段落均无新规则**（仅「No new … rules」类占位），Bridge **自动跳过审批**（`decision=auto_skipped`），本批 `source_event_ids` 仍会计入已消费，不会反复生成空提案。
- 10 条事件可能来自 10 个不同 KOL，也可能少于 10 个（同一 KOL 多次编辑各占一条）。提案卡片展示 `sample_identity_count`（涉及多少位 KOL）。

## 收敛度量（是否「越来越准」）

- `edit_distance ∈ [0,1]`（0=照发，1=完全重写）已对每封产出并入库。
- `GET /learning/edit-distance-trend`（overview 内置近 90 天按周聚合）给出 `avg_edit_distance`、`was_edited_rate`、按时间桶趋势、`style_approval_markers`（趋势图绿色竖线）与 `channel_trends`（shortlist 决策 / 驳回 / 复盘多通道）。
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

## 发现决策学习（discover 闭环，新增）

把 shortlist 三类操作员决策变成学习样本，夜间蒸馏出 **SPU 级**与**品类级**两条 KOL 评判标准，回灌到下一轮发现。

### 采集

| 操作 | 入口 | 采集内容 |
|------|------|----------|
| 批准 shortlist | 列表行点赞「标注」+ Approve 二级弹窗 | 行级标注暂存；弹窗填共享标签+评论并汇总已标注 KOL 后一并提交 |
| 从 shortlist 移除 | 行内「从 shortlist 移除」→ 反馈弹窗 | 单个 KOL 标签+评论 |
| 转到其他活动 | `KolTransferCampaignDialog` | 标签 + 原文 reason 即评论 |

- 每个 KOL 落一条 `shortlist_decision_learning` 事件（`kol_conversation_events`），payload 含 **决策时刻冻结的 KOL 特征快照**（creator_type / followers / region / content_pillars / nox_* 尽调 / veedcrawl reels 数据含 extract_summary 与 last_reel_* / agent 各评分）。
- **标签必选**（提交的标签会对照 Bridge 词表严格校验，失效标签返回 422 `decision_tags_invalid`，避免被静默归为 `other`）；**评论在早期必填**：该 SPU 样本数 < `KOL_DISCOVERY_COMMENT_MIN_SAMPLES`（默认 50，SQL COUNT 精确计数）时必填，达标后选填。弹窗会显示进度，**转移弹窗同样适用此策略**。
- 采集失败**不阻塞主操作**：Console 重试一次（请求路径上限 2 次尝试）→ audit `learning.shortlist_decision_failed`（含完整 `replay_body`）→ 前端 toast 警告；学习页「Discovery 决策学习」面板可**一键补录**（`POST /learning/replay-shortlist-capture`）。紧急回滚：`KOC_DISCOVERY_FEEDBACK_REQUIRED=false`（跳过反馈弹窗、后端跳过校验；Bridge 不可达时 requirements 降级，弹窗同样不阻塞）。

### 蒸馏（夜间，LIVE-only）

| Job | 触发 | 产出 |
|-----|------|------|
| `apply_discovery_policy` | 某 SPU/品类组样本 ≥ `KOL_DISCOVERY_LEARNING_BATCH_SIZE`（默认 10） | pending `approval.discovery_learning_proposal`（每组一份，含 `sample_identity_count`）→ 批准后合并入 `discovery_criteria:spu:<sku>` / `discovery_criteria:category:<slug>` policy；任务前置做未归类 SKU 的 LLM 品类推断。蒸馏分页读取窗口内全部未消费样本（非单页 500 上限）。**单次 LLM 蒸馏**默认最多 `KOL_DISCOVERY_LEARNING_DISTILL_MAX_SAMPLES`（25）条最新样本，避免 69+ 条全量快照撑爆本地 LLM 代理；批准消费的是本批 `source_event_ids`，其余样本留待下一批。蒸馏 prompt 会引用该 scope 近期的 `discovery_proposal_rejected` 驳回反馈（被否原因不重提）。提案末尾的 **Context notes / 背景说明**（批次规模、行动构成等）仅在审批页展示，合并 policy 与发现 brief 时会剥离（与邮件风格学习一致） |
| `mine_discovery_tags` | 评论中某原因出现 ≥ `KOL_DISCOVERY_TAG_MINE_MIN_COUNT`（默认 5） | `discovery_decision_tags` 中 `status=proposed` 的新标签 → 学习页「Discovery 决策学习」面板批准后即出现在反馈弹窗。LLM 引用的 example 必须能在源评论中验证（防虚报频次，验证失败 `examples_not_found_in_comments` 忽略） |

两个 job 均在 `nightly` / `distill` 套件内；审计走 `kol_learning_job_runs`。

### 消费

- 活动启动 / rediscover 时，Console 从 bridge 拉取该 SKU 的 SPU + 品类标准，拼入 brief 的 `# learned_discovery_criteria` 段（SPU 优先、品类补足；上限 `KOC_DISCOVERY_LEARNED_CRITERIA_MAX_CHARS`，默认 4000；开关 `KOC_DISCOVERY_LEARNED_CRITERIA`，bridge 不可用时跳过不阻塞 launch）。
- `instagram-kol-discovery` 技能按「硬性门槛 > 学习标准 > 默认评分」优先级应用（见技能 *Learned Criteria* 章节）；改技能后需 `sync skills`。

### 审批与面板

- 发现标准提案在「待审批 → 发现标准」Tab，**专用卡片视图**（`DiscoveryLearningProposalView`：学习层级 / **涉及 KOL 数** / 样本构成 / 增量正文 / 合并效果对比 / 当前标准展开）；批准即合并 policy，驳回记 `discovery_proposal_rejected` 事件、样本留待下一批且下次蒸馏避开被否建议。
- 学习页新增「Discovery 决策学习」面板（`DiscoveryLearningPanel.tsx`）：**蒸馏批次进度**、待审批提案、标签提案审批、品类映射、**已学标准（按产品 SKU 列表）**、**待补录样本**、最近样本。Workflow 引导条与 `channel_trends.shortlist_decisions` 已纳入 discover 通道。
- 品类可在产品页「品类」字段人工修正（`ProductCategoryField.tsx`），人工值不会被 LLM 覆盖。
- 实现：Bridge `discovery_decision_tags.py` / `discovery_decision_learning.py` / `learning_discovery.py`；Console `discovery_feedback.py` / `learned_criteria.py`。

## 关联模块

- [approvals](../approvals/GUIDE.md) — 批准学习提案 / 合作复盘提案
- [policies](../policies/GUIDE.md) — policy 预览（含 `outcome_strategy`）
- [kols](../kols/GUIDE.md) — 归档合作触发复盘
- 升格后需运行 `playground/learning/sync_skills.py`（操作员文档见根 README）
- **回信策略升格**：`StrategyPromotionPanel`（`reply_strategy` → `references/learned/<goal>.md`）
- **合作复盘升格**：`OutcomePromotionPanel`（`outcome_strategy` → `references/learned/<goal>.outcome.md`）

## 闭环步骤（5 步）

见 `LearningWorkflowStepper.tsx`：信号积累（含 shortlist 决策）→ 提案（编辑或发现）→ 待审批（style / 发现标准 / 策略）→ 策略反哺 →（可选）sync skills

## 手动操作（页内 §2）

| 操作 | 含义 | 环境 |
|------|------|------|
| **运行套件** | 批量 cron 任务（采集 / 蒸馏 / 定价等，依套件） | 固定 LIVE；可先「仅预览」 |
| **生成学习提案** | 仅 `apply_edit_policy`：达批次阈值 → pending `approval.style_learning_proposal` | 页顶 TEST/LIVE |

「蒸馏 / 夜间」套件非预览执行时，也会顺带生成学习提案（与右侧按钮同逻辑）。组件：`LearningManualTriggerSection.tsx`；套件说明文案：`SUITE_OPERATOR_HINTS`（`domainLabels.ts`）。

**502 / 超时：** 生成学习提案含 LLM 蒸馏，Bridge 侧常需 **1–3 分钟**。Console 默认 Bridge 读超时 60s 会误报 502；`propose-edit-policy` / `run-jobs` 已用 `KOC_BRIDGE_LEARNING_TIMEOUT_SEC`（默认 300s）。**批准** `approval.style_learning_proposal`（及 outcome/discovery 学习提案）同样走 LLM 合并，Console `POST /approvals/.../approve` 已对该类 fact 使用同一学习超时（300s）。操作员见「生成中」提示，勿连点。

**502（Bridge 重启中）：** 学习页大量接口同时 502、而 `failed-shortlist-captures` / `gateway-approvals` 仍 200，通常是 **kol-ops-bridge 正在重启**（`connection refused`）。等 Bridge 就绪后刷新即可。

**数据为空 / 仍显示「今夜可蒸馏」：** 若 Bridge `GET /health` 的 `db_path` 指向 `profiles/.../home/.hermes/kol-ops-bridge/cal.db` 而生产样本在 `~/.hermes/kol-ops-bridge/cal.db`，说明进程 `HOME` 与 `HERMES_HOME` 不一致。`start.sh bridge` 与 `run_learning_cron.sh` 会导出 `HERMES_KOL_OPS_CAL_DB=$HERMES_HOME/kol-ops-bridge/cal.db`；手动启动 Bridge 时也应设置该变量或保证 `HERMES_HOME` 指向含数据的目录。

**样本已满但未生成提案：** 「今夜可蒸馏 / 待蒸馏」仅表示**未消费样本数 ≥ 批次阈值**，不保证夜间 LLM 已成功。学习页 Discovery 面板会展示 `last_distill_job`（最近一次 `apply_discovery_policy` 的 `ok` / `error` / `skipped`）；`error` 时常见原因为 Zenmux / 本地代理 **402 配额用尽** 或 **429 限流**（审计：`GET /learning/job-runs?job_name=apply_discovery_policy`，日志：`~/.hermes/logs/learning/nightly.log`）。修复模型配额后，可在学习页手动运行 **distill** 套件补跑；Bridge 会对每组单独蒸馏，单组 LLM 失败不会阻塞其他组。

**必须 LLM：** 编辑学习提案不再静默回退「规则聚合」。LLM 失败返回 503 并说明原因。Bridge 优先用当前 `HERMES_HOME`（profile）解析出的 HTTP 端点；若 profile 本地代理（如 `127.0.0.1:4000`）失败，会自动回退根目录 `~/.hermes/config.yaml` 的模型。standalone `serve.py` 启动时会加载 `~/.hermes/.env` 与 `plugins/kol-ops-bridge/.env`。若 HTTPS 报证书错误，在 bridge venv 安装 `certifi`；若要走 `call_llm`，还需安装 `openai`。可用 `KOL_LEARNING_LLM_*` 覆盖。
