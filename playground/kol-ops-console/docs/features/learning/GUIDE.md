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
| FE | `LearningPage.tsx`, `LearningManualTriggerSection.tsx`, `LearningWorkflowStepper.tsx`, `StrategyPromotionPanel.tsx` |
| BE | `routers/learning.py` |
| 外链 | `agent_prj/docs/kol-learning-tier1-implementation.md` |

## 主要 API

| 方法 | 路径 | Bridge 代理 |
|------|------|-------------|
| GET | `/learning/overview` | overview |
| POST | `/learning/run-jobs` | scheduled jobs（LIVE） |
| GET | `/learning/job-runs`, `edit-events`, `reject-events` | 同名 |
| POST | `/learning/propose-edit-policy` | apply-edit-policy |
| POST | `/learning/backfill-edit-learning` | backfill sent drafts missing `draft_edit_learning` |
| POST | `/learning/promote-strategy` | promote-strategy |
| GET | `/learning/policies/{scope}` | policies |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 批准学习提案
- [policies](../policies/GUIDE.md) — policy 预览
- 升格后需运行 `playground/learning/sync_skills.py`（操作员文档见根 README）

## 闭环步骤（5 步）

见 `LearningWorkflowStepper.tsx`：信号积累 → 提案 → 待审批 → 策略反哺 →（可选）sync skills

## 手动操作（页内 §2）

| 操作 | 含义 | 环境 |
|------|------|------|
| **运行套件** | 批量 cron 任务（采集 / 蒸馏 / 定价等，依套件） | 固定 LIVE；可先「仅预览」 |
| **生成学习提案** | 仅 `apply_edit_policy`：达批次阈值 → pending `approval.style_learning_proposal` | 页顶 TEST/LIVE |

「蒸馏 / 夜间」套件非预览执行时，也会顺带生成学习提案（与右侧按钮同逻辑）。组件：`LearningManualTriggerSection.tsx`；套件说明文案：`SUITE_OPERATOR_HINTS`（`domainLabels.ts`）。

**502 / 超时：** 生成学习提案含 LLM 蒸馏，Bridge 侧常需 **1–3 分钟**。Console 默认 Bridge 读超时 60s 会误报 502；`propose-edit-policy` / `run-jobs` 已用 `KOC_BRIDGE_LEARNING_TIMEOUT_SEC`（默认 300s）。操作员见「生成中」提示，勿连点。
