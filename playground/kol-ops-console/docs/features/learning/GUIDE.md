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
| FE | `LearningPage.tsx`, `LearningWorkflowStepper.tsx`, `StrategyPromotionPanel.tsx` |
| BE | `routers/learning.py` |
| 外链 | `agent_prj/docs/kol-learning-tier1-implementation.md` |

## 主要 API

| 方法 | 路径 | Bridge 代理 |
|------|------|-------------|
| GET | `/learning/overview` | overview |
| POST | `/learning/run-jobs` | scheduled jobs（LIVE） |
| GET | `/learning/job-runs`, `edit-events`, `reject-events` | 同名 |
| POST | `/learning/propose-edit-policy` | apply-edit-policy |
| POST | `/learning/promote-strategy` | promote-strategy |
| GET | `/learning/policies/{scope}` | policies |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 批准学习提案
- [policies](../policies/GUIDE.md) — policy 预览
- 升格后需运行 `playground/learning/sync_skills.py`（操作员文档见根 README）

## 闭环步骤（5 步）

见 `LearningWorkflowStepper.tsx`：信号积累 → 提案 → 待审批 → 策略反哺 →（可选）sync skills
