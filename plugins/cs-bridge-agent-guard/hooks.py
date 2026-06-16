"""Block unsafe QuickCEP send-email in povison-cs gateway runs."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Union

HookResult = Optional[Union[None, Dict[str, str]]]

_CS_SESSION_PREFIX = "povison-cs:"
_SEND_EMAIL_RE = re.compile(
    r"(?is)"
    r"quickcep_cli\.py\s+send-email|"
    r"/im/message/operator/sendEmail|"
    r"send-email\s+<"
)


def _guard_enabled() -> bool:
    return os.environ.get("CS_BRIDGE_AGENT_GUARD", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _session_key(session_id: str, task_id: str = "") -> str:
    return (session_id or task_id or "").strip()


def _cs_session(session_id: str, task_id: str = "") -> bool:
    return _session_key(session_id, task_id).startswith(_CS_SESSION_PREFIX)


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
) -> HookResult:
    del tool_call_id
    if not _guard_enabled():
        return None
    if not _cs_session(session_id, task_id):
        return None
    if tool_name != "terminal":
        return None
    command = str(args.get("command") or args.get("cmd") or "")
    if _SEND_EMAIL_RE.search(command):
        return {
            "action": "block",
            "message": (
                "quickcep send-email is forbidden for povison-cs automation — "
                "use draft-save only; human operators send from QuickCEP UI."
            ),
        }
    return None
