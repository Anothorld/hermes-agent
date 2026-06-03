# 策略编辑

## 功能说明

编辑公司/用户/升级相关 **Markdown 策略**（风格、规则），含历史版本与升级规则解析。影响 Agent 系统提示与 learning scope。

## 操作员路径

| 路径 | 页面 |
|------|------|
| `/policies` | `PolicyEditorPage.tsx` |

## 关键文件

| 层 | 文件 |
|----|------|
| FE | `PolicyEditorPage.tsx` |
| BE | `routers/policies.py` |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PUT | `/policies/{scope}` | 读写 MD（scope 如 company_style） |
| GET | `/policies/{scope}/history` | 版本历史 |
| GET | `/policies/escalation-rules/parsed` | 解析后的升级规则 |

## 关联模块

- [learning](../learning/GUIDE.md) — `GET /learning/policies/{scope}`
- [escalations](../escalations/GUIDE.md) — 升级规则生效
- RBAC：`test_policy_rbac.py`

## UX

大段 Markdown 编辑：保存需明确成功/失败提示；避免技术 scope 名直接作为主标题（可用中文说明对应项）。
