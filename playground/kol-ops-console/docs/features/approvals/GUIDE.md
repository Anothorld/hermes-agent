# 待审批

## 功能说明

操作员处理 AI 产生的 **待批准事实**（尤其是 `approval.reply_draft` 回信草稿）。支持批准、结构化驳回（标签+说明+建议改法）、与学习提案 Tab。日常最高频路径之一。

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
- `DraftEditDiffPanel` 对比 Agent 草稿 vs Gmail 已发正文
- **`approval.style_learning_proposal`**：按 policy 范围分组（非单个 KOL）；组头为「跨 KOL 编辑学习」，勿把 anchor `identity_id` 上的 `@handle` 当作本案 KOL。提案正文展示 `sample_identity_count` / 编辑样本数 / 涉及操作员。
  - 提案为**增量修订（delta）**：蒸馏已读取当前 policy 作基线，仅产出新增/`ADJUST:`/`REMOVE:`；卡片可展开「当前 policy」对比（`CurrentPolicyPreview`）。
  - 批准合并模式 `KOL_STYLE_LEARNING_MERGE_MODE`：`append`（默认累加）/`replace_section`（替换最新 Approved 节，历史留版本链）/`llm_compress`（二次 LLM 合并去矛盾，较慢，失败回退 append）。
  - **驳回**写 `style_proposal_rejected` 负反馈事件（不开升级），下次蒸馏 prompt 会引用「上次被否原因」以避免重复。
- **草稿来源标签**（`draft_origin` / `draft_origin_label`，仅 `approval.reply_draft`）：
  - `KOL回信自动` — 入站 dispatcher 正常起草
  - `升级恢复稿` — 操作员 resume 升级后自动起草（含 `linked_escalation_id`）
  - `操作员追信` — followup-draft / `kol-proactive-followup`
  - `追信占位(已废弃)` — 升级 open 期间的 chase 占位（Bridge 现已 409 拦截）；若升级仍 open 会提示先处理升级
- **升级 resume 后**：Console 返回 `draft_followup`：`expected`（新稿将生成）/ `already_pending`（已有 linked 预览稿）/ `in_flight`（预览稿生成中）
- **`approval.reply_draft.chase_supersede`**：对方追信后系统自动换新草稿时会出现；卡片顶部显示「已针对追信更新草稿」提示，请核对正文是否回应跟进。若上一版已在 Gmail 生成草稿，系统会尝试自动删除；失败时需运营在 Gmail 草稿箱手动删除。
- **批准后的 Gmail 草稿**：与在 Gmail 里点「回复」类似，会带上可折叠的引用块（`…`），且只引用对方**最新一封**正文，不会把整段 `>` 嵌套历史全部展开。若草稿仍是满屏 `>` 引用，说明是旧版 bridge 生成，可驳回后重新批准或手动在 Gmail 里回复。
- **首次冷启动触达**（`kol-cold-outreach` / `kol-reengagement-outreach`，主题通常不是 `Re:`）：批准后会在 Gmail **新建独立草稿**（不挂到已有会话 thread）。CAL 里的 `outreach_{活动}_{红人}` 只是系统内部锚点，不是 Gmail thread id；若误按「回信」规则校验会报 400，需更新 bridge 后重试批准。
