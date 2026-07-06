"""Block unsafe QuickCEP send-email in povison-cs gateway runs.

Also notifies the cs-ops-bridge when a CS agent run ends so the bridge can
detect resume runs that finished without applying handoff (ESC36/37 failure
mode). The notification is fire-and-forget via a daemon thread so the gateway
run completion event is never delayed.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Union

log = logging.getLogger(__name__)

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


def _cs_profile_prefix() -> str:
    name = (os.environ.get("CS_OPS_PROFILE") or "povison-cs").strip() or "povison-cs"
    return f"{name}:"


def _post_run_finished_async(session_id: str, completed: bool, interrupted: bool) -> None:
    """Daemon-thread HTTP POST to cs-ops-bridge /internal/run-finished (best-effort)."""
    bridge_base = os.environ.get("CS_OPS_BRIDGE_BASE", "http://127.0.0.1:8081").strip()
    bridge_key = (
        os.environ.get("HERMES_CS_OPS_BRIDGE_KEY")
        or os.environ.get("CS_OPS_BRIDGE_KEY")
        or ""
    )
    if not bridge_base or not bridge_key:
        log.warning(
            "on_session_end: skipping run-finished callback — "
            "CS_OPS_BRIDGE_BASE or HERMES_CS_OPS_BRIDGE_KEY not set"
        )
        return
    url = f"{bridge_base.rstrip('/')}/internal/run-finished"
    body = json.dumps(
        {"session_id": session_id, "completed": completed, "interrupted": interrupted}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Key": bridge_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:
        log.debug("on_session_end: run-finished callback failed session=%s: %s", session_id, exc)


def on_session_end(
    session_id: str = "",
    completed: bool = True,
    interrupted: bool = False,
    **_: Any,
) -> None:
    """Fire-and-forget notify cs-ops-bridge when a CS agent run ends.

    The bridge checks whether a resuming escalation is still stuck (handoff
    not applied) and notifies the operator. Only session_ids matching the
    povison-cs profile prefix are forwarded; everything else is ignored.
    """
    if not session_id or not session_id.startswith(_cs_profile_prefix()):
        return
    thread = threading.Thread(
        target=_post_run_finished_async,
        args=(session_id, completed, interrupted),
        daemon=True,
        name=f"cs-run-finished-{session_id}",
    )
    thread.start()
