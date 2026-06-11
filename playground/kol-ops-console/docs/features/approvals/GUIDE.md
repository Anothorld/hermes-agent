# 待审批

## 功能说明

操作员处理 AI 产生的 **待批准事实**（尤其是 `approval.reply_draft` 回信草稿）。支持批准、结构化驳回（标签+说明+建议改法）、与学习提案 Tab。日常最高频路径之一。

**类型筛选**：原「回信草稿」已拆为 **初邀**（`reply_draft_kind=initial_outreach`，冷启动/再触达首封）与 **来信回复**（`reply_draft_kind=inbound_reply`，KOL 来信后的回复、追信、升级恢复稿等）。分类规则与 `backend/app/reply_draft_kind.py` 一致（`child_skill`、草稿 `kind`、内部 thread 锚点）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/approvals` | `ApprovalsPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `ApprovalsPage.tsx`；`ApprovalDetailPanel`, `ApprovalContextCard`, `ApprovalActionBar`, `RejectCorrectionModal`, `DraftEditDiffPanel` |
| BE | `routers/approvals.py` |
| 常量 | `constants/rejectTags.ts` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/approvals` | 待办列表（可按 campaign 过滤） |
| POST | `/approvals/{fact_path}/approve` | 批准 |
| POST | `/approvals/approval.reply_draft/reject` | 结构化驳回（见根 `README.md` 示例） |
| POST | `/approvals/.../refine` | 要求 Agent 改写 |

## 关联模块

- [learning](../learning/GUIDE.md) — `approval.style_learning_proposal` 等
- [gmail](../gmail/GUIDE.md) — 批准后 bridge 用绑定邮箱发送
- [gate-metrics](../gate-metrics/GUIDE.md) — 驳回标签统计

## UX 注意

- 驳回用 **标签多选** + 中文说明，勿暴露英文 fact key（除非设置里开启「显示原始字段」）
- `DraftEditDiffPanel` 对比 Agent 草稿 vs Gmail 已发正文；**仅出现在已处理（批准/驳回）历史 Tab**，待审批中的回信草稿不展示（避免误用同 KOL+活动 下上一封已发邮件的学习记录）
- **`approval.style_learning_proposal`**：按 policy 范围分组（非单个 KOL）；组头为「跨 KOL 编辑学习」，勿把 anchor `identity_id` 上的 `@handle` 当作本案 KOL。提案正文展示 `sample_identity_count` / 编辑样本数 / 涉及操作员。
  - 提案为**增量修订（delta）**：蒸馏已读取当前 policy 作基线，仅产出新增/`ADJUST:`/`REMOVE:`；卡片可展开「当前 policy」对比（`CurrentPolicyPreview`）。
  - **`### Context notes`**（批次大小、无效样本、与基线对比等）**仅展示在审批卡片**，批准合并时自动剥离，**不写入** `company_style` / `reply_strategy` policy。
  - 若某段仅有「无新规则」类说明、无可执行 bullet，批准时**跳过该段 policy 写入**（样本仍标记已消费），合并预览显示「保持不变」。
  - 批准合并默认 `KOL_STYLE_LEARNING_MERGE_MODE=llm_compress`：**LLM 智能合并**（去重、应用 ADJUST/REMOVE、保留 preamble）；LLM 失败回退确定性 patch。审批卡预览为 patch 近似（`preview_note`），实际批准走 LLM。可选 `replace_section`（仅 patch）/`append`（逐批累加）。
  - **驳回**写 `style_proposal_rejected` 负反馈事件（不开升级），下次蒸馏 prompt 会引用「上次被否原因」以避免重复。
- **`approval.discovery_learning_proposal`**（类型筛选「发现标准」）：发现决策学习提案，按 `discovery_criteria:spu:<sku>` / `discovery_criteria:category:<slug>` 范围分组，组头为「KOL 发现标准学习 · 产品/品类 …」。专用卡片视图（`DiscoveryLearningProposalView`）展示学习层级 / 样本数 / 批准-移除-转移构成 / 增量修订正文，并支持「批准后 policy 合并效果」对比（`PolicyMergeDiffPreview`，Bridge merge-preview 已支持 `discovery_criteria:*`）与「当前标准」展开。批准即合并入对应 policy 并被下一轮发现 brief 引用；驳回写 `discovery_proposal_rejected` 事件（不开升级），样本留待下一批蒸馏、**下次蒸馏 prompt 会引用被否原因避免重复**。详见 [learning](../learning/GUIDE.md#发现决策学习discover-闭环新增)。
- **草稿来源标签**（`draft_origin` / `draft_origin_label`，仅 `approval.reply_draft`）：
  - `KOL回信自动` — 入站 dispatcher 正常起草
  - `升级恢复稿` — 操作员 resume 升级后自动起草（含 `linked_escalation_id`）
  - `操作员追信` — followup-draft / `kol-proactive-followup`
  - `追信占位(已废弃)` — 升级 open 期间的 chase 占位（Bridge 现已 409 拦截）；若升级仍 open 会提示先处理升级
- **升级 resume 后**：Console 返回 `draft_followup`：`expected`（新稿将生成）/ `already_pending`（已有 linked 预览稿）/ `in_flight`（预览稿生成中）
- **`approval.reply_draft.chase_supersede`**：对方追信后系统自动换新草稿时会出现；卡片顶部显示「已针对追信更新草稿」提示，请核对正文是否回应跟进。若上一版已在 Gmail 生成草稿，系统会尝试自动删除；失败时需运营在 Gmail 草稿箱手动删除。
- **批准后的 Gmail 草稿**：与在 Gmail 里点「回复」类似，会带上可折叠的引用块（`…`），且只引用对方**最新一封**正文，不会把整段 `>` 嵌套历史全部展开。若草稿仍是满屏 `>` 引用，说明是旧版 bridge 生成，可驳回后重新批准或手动在 Gmail 里回复。
- **初邀 Tab**（`reply_draft_kind=initial_outreach`）：首次冷启动触达（`kol-cold-outreach` / `kol-reengagement-outreach`，主题通常不是 `Re:`）。批准后会在 Gmail **新建独立草稿**（不挂到已有会话 thread）。CAL 里的 `outreach_{活动}_{红人}` 只是系统内部锚点，不是 Gmail thread id；若误按「回信」规则校验会报 400，需更新 bridge 后重试批准。
- **来信回复 Tab**（`reply_draft_kind=inbound_reply`）：KOL 来信后的回复草稿；卡片可展开对方来信。批准后按「回信」规则在原 Gmail 线程内建草稿。
