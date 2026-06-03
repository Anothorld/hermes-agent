# 门禁效果看板

## 功能说明

展示回信草稿 **首轮通过率**、高频 **驳回标签** 等指标，帮助操作员与运营评估 AI 质量（非开发监控）。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/metrics` | `GateMetricsPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `GateMetricsPage.tsx` |
| BE | `routers/admin.py`（`GET /admin/gate-metrics`） |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/gate-metrics` | 聚合指标（带 `env`） |

## 关联模块

- [approvals](../approvals/GUIDE.md) — 驳回标签来源 `rejectTags.ts`
- [auth-settings](../auth-settings/GUIDE.md) — 同 `admin` router

## UX

图表/数字配 **中文说明**（何为「首轮通过」）；可按活动或时间筛选时写清筛选含义。
