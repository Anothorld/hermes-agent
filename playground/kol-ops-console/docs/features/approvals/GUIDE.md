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
- **`approval.reply_draft.chase_supersede`**：对方追信后系统自动换新草稿时会出现；卡片顶部显示「已针对追信更新草稿」提示，请核对正文是否回应跟进。
