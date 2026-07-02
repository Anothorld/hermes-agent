"""Block unsafe QuickCEP send-email in povison-cs gateway runs."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, Optional, Union

HookResult = Optional[Union[None, Dict[str, str]]]

_SEND_GUARD = None


def _send_guard():
    global _SEND_GUARD
    if _SEND_GUARD is None:
        path = Path(__file__).with_name("send_guard.py")
        spec = importlib.util.spec_from_file_location("cs_bridge_send_guard", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load send_guard from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SEND_GUARD = mod
    return _SEND_GUARD


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    run_kind: str = "",
    **_: Any,
) -> HookResult:
    return _send_guard().pre_tool_block(
        tool_name=tool_name,
        args=args,
        task_id=task_id,
        session_id=session_id,
        run_kind=run_kind,
    )
