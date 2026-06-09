"""Detect agent attempts to run MySQL queries outside mysql_execute_sql."""

from __future__ import annotations

import re
from typing import Optional

_BLOCK_MESSAGE = (
    "禁止 Agent 直接执行 MySQL 查询（terminal / execute_code / sql_executor.py / mysql CLI 均已屏蔽）。"
    "请仅使用 mysql_execute_sql 工具；执行前会经 Hermes clarify 人工审核，"
    "支持「批准本次执行」与「批准本会话内免审」。"
    "Schema 元数据请用 schema_reader.py（只读 information_schema）；"
    "sql_executor.py 仅允许 --validate 或 --test。"
)

_MYSQL_CLI = re.compile(
    r"(?:^|[\s|;])?(?:mysql|mariadb)\b[^\n]*?(?:-e\b|--execute=)",
    re.IGNORECASE,
)

_PYTHON_SQL_BYPASS = re.compile(
    r"(?:import\s+sql_executor|from\s+sql_executor\s+import|"
    r"execute_query\s*\(|sql_executor\.execute|"
    r"pymysql\.connect\s*\()",
    re.IGNORECASE,
)

_EXECUTOR_PATH = re.compile(
    r"mysql_tools[/\\]sql_executor\.py|scripts[/\\]sql_executor\.py",
    re.IGNORECASE,
)


def _normalize_command(command: str) -> str:
    return (command or "").strip()


def _is_sql_executor_validate_or_test(command: str) -> bool:
    lowered = command.lower()
    if "sql_executor.py" not in lowered:
        return False
    return "--validate" in lowered or "--test" in lowered


def is_blocked_terminal_command(command: str) -> bool:
    """Return True when a terminal command attempts direct SQL execution."""
    cmd = _normalize_command(command)
    if not cmd:
        return False

    if _is_sql_executor_validate_or_test(cmd):
        return False

    if "sql_executor.py" in cmd.lower():
        return True
    if _EXECUTOR_PATH.search(cmd) and not _is_sql_executor_validate_or_test(cmd):
        return True
    if _MYSQL_CLI.search(cmd):
        return True
    if _PYTHON_SQL_BYPASS.search(cmd):
        return True
    return False


def is_blocked_execute_code(code: str) -> bool:
    """Return True when execute_code attempts direct SQL execution."""
    src = _normalize_command(code)
    if not src:
        return False

    lowered = src.lower()
    if "schema_reader" in lowered:
        if (
            "sql_executor" not in lowered
            and "execute_query" not in lowered
            and "pymysql" not in lowered
        ):
            return False

    if _PYTHON_SQL_BYPASS.search(src):
        return True
    if _EXECUTOR_PATH.search(src):
        return True
    if _MYSQL_CLI.search(src):
        return True
    if "sql_executor.py" in lowered and not _is_sql_executor_validate_or_test(src):
        return True
    return False


def block_message() -> str:
    """Operator-facing block reason for pre_tool_call hooks."""
    return _BLOCK_MESSAGE


def check_tool_call(tool_name: str, args: dict) -> Optional[str]:
    """Return a block message if the tool call bypasses mysql_execute_sql."""
    if tool_name == "terminal":
        command = str(args.get("command", ""))
        if is_blocked_terminal_command(command):
            return block_message()
        return None

    if tool_name == "execute_code":
        code = str(args.get("code", ""))
        if is_blocked_execute_code(code):
            return block_message()
        return None

    return None
