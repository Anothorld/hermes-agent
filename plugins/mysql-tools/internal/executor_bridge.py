"""Bridge to profile-local sql_executor.py implementation."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home

_DEBUG_LOG = "/Users/arnold/agent_prj/.cursor/debug-18619c.log"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "18619c",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "runId": "pre-fix",
        }
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion


def _scripts_dir() -> Path:
    return get_hermes_home() / "scripts" / "mysql_tools"


def _probe_config_resolution(scripts: Path) -> dict[str, Any]:
    """Inspect how config_loader resolves mysql_tools.yaml at runtime."""
    info: dict[str, Any] = {
        "env_HERMES_HOME": os.environ.get("HERMES_HOME", ""),
        "env_HERMES_PROFILE": os.environ.get("HERMES_PROFILE", ""),
        "get_hermes_home": str(get_hermes_home()),
        "scripts_dir": str(scripts),
        "scripts_exists": scripts.is_dir(),
        "sql_executor_exists": (scripts / "sql_executor.py").is_file(),
    }
    scripts_str = str(scripts)
    if scripts_str not in sys.path and scripts.is_dir():
        sys.path.insert(0, scripts_str)
    try:
        from config_loader import get_config_dir, get_target_database, load_config

        config_dir = get_config_dir()
        config_path = config_dir / "mysql_tools.yaml"
        loaded = load_config()
        info.update(
            {
                "config_dir": str(config_dir),
                "config_path": str(config_path),
                "config_exists": config_path.exists(),
                "target_database": get_target_database(),
                "config_keys": list(loaded.keys()),
            }
        )
    except Exception as exc:
        info["config_probe_error"] = repr(exc)

    root = Path(os.path.expanduser("~/.hermes"))
    resolved_home = Path(info["get_hermes_home"])
    info["alt_config_paths"] = [
        {
            "path": str(p),
            "exists": p.exists(),
        }
        for p in (
            root / "profiles" / "data-analyst" / "config" / "mysql_tools.yaml",
            resolved_home / "config" / "mysql_tools.yaml",
            resolved_home / "profiles" / "data-analyst" / "config" / "mysql_tools.yaml",
        )
    ]
    return info


def is_mysql_executor_available() -> bool:
    """Return True when profile scripts and sql_executor exist."""
    scripts = _scripts_dir()
    return (scripts / "sql_executor.py").is_file()


def execute_sql_query(sql: str, database: Optional[str] = None) -> Dict[str, Any]:
    """Execute SQL via the profile-local sql_executor module."""
    scripts = _scripts_dir()
    probe = _probe_config_resolution(scripts)
    probe["database_arg"] = database
    _debug_log("H1-H5", "executor_bridge.py:execute_sql_query", "config and path probe", probe)

    if not scripts.is_dir():
        _debug_log(
            "H2",
            "executor_bridge.py:execute_sql_query",
            "scripts dir missing",
            {"scripts_dir": str(scripts)},
        )
        return {"error": f"MySQL 工具脚本目录不存在: {scripts}"}

    scripts_str = str(scripts)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)

    from sql_executor import execute_query  # noqa: WPS433 — dynamic profile import

    result = execute_query(sql, database)
    if result.get("error"):
        _debug_log(
            "H1-H3",
            "executor_bridge.py:execute_sql_query",
            "execute_query returned error",
            {
                "error": result.get("error"),
                "database_arg": database,
                "target_database": probe.get("target_database"),
                "config_exists": probe.get("config_exists"),
            },
        )
    return result
