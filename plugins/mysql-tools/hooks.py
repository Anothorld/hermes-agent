"""pre_tool_call hooks — block direct MySQL execution bypassing mysql_execute_sql."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional, Union

HookResult = Optional[Union[None, Dict[str, str]]]


def _load_approval_gate():
    cached_name = "hermes_plugins.mysql_tools.approval_gate"
    import sys

    cached = sys.modules.get(cached_name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("internal") / "approval_gate.py"
    spec = importlib.util.spec_from_file_location(cached_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load approval_gate from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cached_name] = module
    spec.loader.exec_module(module)
    return module


def _load_direct_sql_guard():
    cached_name = "hermes_plugins.mysql_tools.direct_sql_guard"
    import sys

    cached = sys.modules.get(cached_name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("internal") / "direct_sql_guard.py"
    spec = importlib.util.spec_from_file_location(cached_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load direct_sql_guard from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cached_name] = module
    spec.loader.exec_module(module)
    return module


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> HookResult:
    del task_id, session_id, tool_call_id

    guard = _load_direct_sql_guard()
    message = guard.check_tool_call(tool_name, args)
    if message:
        return {"action": "block", "message": message}
    return None


def on_session_end(session_id: str = "", **_: Any) -> None:
    """Clear session-wide SQL approval skip on session boundary."""
    gate = _load_approval_gate()
    gate.clear_session(str(session_id or ""))
