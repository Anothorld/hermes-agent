"""MySQL tools plugin — clarify-gated SQL execution for data-analyst profile."""

from __future__ import annotations

from . import hooks
from .schemas import MYSQL_EXECUTE_SQL_SCHEMA
from .tools import _check_mysql_tools_available, _handle_mysql_execute_sql


def register(ctx) -> None:
    """Register mysql_execute_sql and terminal guard hook."""
    ctx.register_tool(
        name="mysql_execute_sql",
        toolset="mysql_tools",
        schema=MYSQL_EXECUTE_SQL_SCHEMA,
        handler=_handle_mysql_execute_sql,
        check_fn=_check_mysql_tools_available,
        emoji="🗄️",
    )
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("on_session_end", hooks.on_session_end)
