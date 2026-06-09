"""Tool handlers for mysql-tools plugin."""

from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

from .internal.approval_gate import request_sql_execution_approval
from .internal.executor_bridge import execute_sql_query, is_mysql_executor_available


def _check_mysql_tools_available() -> bool:
    return is_mysql_executor_available()


def _handle_mysql_execute_sql(args: dict[str, Any], **kwargs: Any) -> str:
    sql = str(args.get("sql", "")).strip()
    if not sql:
        return tool_error("sql 参数不能为空。", invalid_input=True)

    database = args.get("database")
    db_override = str(database).strip() if database else None

    # region agent log
    import json
    import os
    import time

    try:
        payload = {
            "sessionId": "18619c",
            "hypothesisId": "H4",
            "location": "tools.py:_handle_mysql_execute_sql",
            "message": "mysql_execute_sql args",
            "data": {
                "database_raw": database,
                "database_override": db_override,
                "has_database_key": "database" in args,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "pre-fix",
        }
        with open("/Users/arnold/agent_prj/.cursor/debug-18619c.log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion

    approved, message = request_sql_execution_approval(
        sql=sql,
        database=db_override,
        session_id=str(kwargs.get("session_id") or kwargs.get("task_id") or ""),
        clarify_callback=kwargs.get("clarify_callback"),
    )
    if not approved:
        return tool_error(message or "SQL 执行未通过人工审核。")

    result = execute_sql_query(sql, db_override)
    if result.get("error"):
        return tool_error(str(result["error"]), sql=result.get("sql"))
    return tool_result(result)
