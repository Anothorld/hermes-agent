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
- **`approval.style_learning_proposal`**：按 policy 范围分组（非单个 KOL）；组头为「跨 KOL 编辑学习」，勿把 anchor `identity_id` 上的 `@handle` 当作本案 KOL。提案正文展示 `sample_identity_count` / 编辑样本数。
