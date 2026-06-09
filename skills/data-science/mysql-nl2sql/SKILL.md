---
name: mysql-nl2sql
version: 1.5.0
description: NL to safe MySQL queries via clarify-gated executor tool.
triggers:
  - 查询数据
  - 生成SQL
  - 数据库查询
  - NL2SQL
  - mysql
---

# MySQL NL2SQL 工具集

## 概述

通过双账户体系安全实现自然语言到 SQL 的查询：

- **Schema Explorer**: 只读 information_schema，提取表/字段元数据（`schema_reader.py`）
- **SQL 查数**: **仅** `mysql_execute_sql` 工具（Hermes clarify 人工审核）

## 人工审核门控（必须）

**禁止** Agent 使用 `terminal` / `execute_code` / `mysql` CLI / `sql_executor.py` 执行数据查询（插件会拦截）。

**唯一**查数入口：

```json
mysql_execute_sql(sql="SELECT ...", database="ads")
```

clarify 审核选项：批准本次执行 / 拒绝 / 批准本会话内免审。

Schema 元数据仍可用 `schema_reader.py`；`sql_executor.py` 仅允许 `--validate` / `--test`（terminal）。

## 工作流程

### Step 1–3: 意图 → Schema → 生成 SQL

同前；Schema 用 `schema_reader.py`。

### Step 4: 验证 SQL（可选）

```bash
SCRIPTS=$HERMES_HOME/scripts/mysql_tools
echo "SELECT 1" | PYTHONPATH=$SCRIPTS python $SCRIPTS/sql_executor.py --validate
```

### Step 5: 执行（仅 mysql_execute_sql）

```json
{"name": "mysql_execute_sql", "arguments": {"sql": "SELECT ...", "database": "ads"}}
```

### Step 6: 解读结果

## clarify 不可用

不得绕过门控。告知用户改在交互式 Hermes CLI/TUI 继续。

## 参考

- [ads 数据库表结构与常用查询](references/ads-database-schema.md)
- [集成说明](agent_prj/docs/mysql-tools-clarify-gate.md)
