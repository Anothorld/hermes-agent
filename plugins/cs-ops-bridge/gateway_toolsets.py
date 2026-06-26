"""Recommended platform_toolsets.api_server for povison-cs gateway runs."""

from __future__ import annotations

# Mirror interactive CLI toolsets but omit delegation (forces direct terminal for
# bridge CLI) and omit code_execution (not listed for cli either; api_server
# default hermes-api-server would add execute_code + delegate_task).
POVISON_CS_API_SERVER_TOOLSETS: tuple[str, ...] = (
    "browser",
    "cronjob",
    "file",
    "memory",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "vision",
    "web",
)
