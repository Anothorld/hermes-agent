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
| GET/PUT | `/policies/{scope}` | 读写 MD（scope 如 company_style；`reply_strategy` / `outcome_strategy` 需 `?env=LIVE`） |
| GET | `/policies/{scope}/history` | 版本历史 |
| GET | `/policies/{scope}/version/{version}` | 指定历史版本内容（对比/预览） |
| POST | `/policies/{scope}/rollback` | 回滚到指定版本（前向写新版本，保留历史；RBAC 同 PUT） |
| GET | `/policies/escalation-rules/parsed` | 解析后的升级规则 |

## 关联模块

- [learning](../learning/GUIDE.md) — `GET /learning/policies/{scope}`
- [escalations](../escalations/GUIDE.md) — 升级规则生效
- RBAC：`test_policy_rbac.py`

## UX

大段 Markdown 编辑：保存需明确成功/失败提示；避免技术 scope 名直接作为主标题（可用中文说明对应项）。

### 异常处理规则（`escalation_rules`）

每条规则的 `suggested_question` 会原样显示在升级页 **「请求操作员答复」**，操作员惯用语言为 **简体中文**。请用 plain language 写清楚「需要决定什么」，避免英文模板或内部 jargon。

示例：

```markdown
### rule_id: paid_quote_over_ceiling
- signals_match: ["compensation.kol_quoted_over_ceiling"]
- severity: high
- suggested_question: "KOL 报价超过活动 paid_ceiling，是否批准提价？如需批准，请在升级答复中说明可接受上限。"
- required_facts_to_resume: ["approval.paid_ceiling_override"]
```

- 历史版本支持「查看」（预览该版本内容）与「回滚」（基于该版本生成新版本，不删历史）。
- 回滚是自动学习「退化护栏」的人工补救：当 `/learning` 的「编辑幅度趋势」告警变差时，可回滚到上一版。
- 人工编辑同样计入收敛度量与护栏；自动学习提案是「建议草稿」，最终以人工把关为准。
