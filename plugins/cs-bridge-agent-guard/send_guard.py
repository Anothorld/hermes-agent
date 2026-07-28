"""Shared QuickCEP send-email guard for povison-cs automation."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

_CS_PROFILE = (os.environ.get("CS_OPS_PROFILE") or "povison-cs").strip() or "povison-cs"
_CS_SESSION_PREFIX = f"{_CS_PROFILE}:"

_QUICKCEP_CLI_COMMAND_RE = re.compile(r"(?is)quickcep_cli(?:\.py)?\b")

_SEND_EMAIL_COMMAND_RE = re.compile(
    r"(?is)"
    r"quickcep_cli(?:\.py)?\s+send-email\b|"
    r"\bsend-email\s+(?:<|\d|\"|'|--)|"
    r"/im/message/operator/sendEmail|"
    r"operator/sendEmail\b|"
    r"message/operator/sendEmail"
)

_BLOCK_MESSAGE = (
    "QuickCEP send-email is forbidden for povison-cs automation — "
    "use cs_bridge_tool draft-save only; human operators send from QuickCEP UI."
)

_CLI_BLOCK_MESSAGE = (
    "Direct quickcep_cli is forbidden for povison-cs automation — "
    "use cs_bridge_tool only (get-messages, draft-save, apply-handoff, …)."
)

_EXECUTE_CODE_BRIDGE_BLOCK_MESSAGE = (
    "Do not wrap cs_bridge_tool in execute_code or subprocess.run — "
    "call the terminal tool once per bridge step (get-escalation, get-messages, "
    "draft-save, apply-handoff, …)."
)

_CS_BRIDGE_TOOL_COMMAND_RE = re.compile(r"(?is)cs_bridge_tool(?:\.py)?\b")

# PR3: edit_memory runs inherit the reply-generation context but are restricted
# to the dedicated Hindsight knowledge tools only — every other tool is blocked
# so the agent can only analyze the operator's edit and retain reusable facts to
# the Knowledge bank (furniture-knowledge), never act on the customer. Auto-
# consolidation (observations_mission) replaces the legacy hindsight_reflect.
EDIT_MEMORY_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "knowledge_retain",
    "knowledge_recall",
})
EDIT_MEMORY_BLOCK_MESSAGE = (
    "edit_memory run is restricted to the Hindsight knowledge tools "
    "(knowledge_retain, knowledge_recall); "
    "all other tools are blocked for this run kind. "
    "Operator factual corrections are retained to the Knowledge bank (furniture-knowledge) "
    "via knowledge_retain; knowledge_recall checks what is already known."
)


def guard_enabled() -> bool:
    return os.environ.get("CS_BRIDGE_AGENT_GUARD", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def manual_send_override() -> bool:
    return os.environ.get("CS_OPS_ALLOW_QUICKCEP_SEND", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def manual_cli_override() -> bool:
    return os.environ.get("CS_OPS_ALLOW_QUICKCEP_CLI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def cs_automation_session(session_id: str = "", task_id: str = "") -> bool:
    key = (session_id or task_id or "").strip()
    return bool(key) and key.startswith(_CS_SESSION_PREFIX)


def povison_profile_active() -> bool:
    profile = (os.environ.get("CS_OPS_PROFILE") or "povison-cs").strip().lower()
    return profile.startswith("povison")


def looks_like_quickcep_send_email(text: str) -> bool:
    return bool(text) and bool(_SEND_EMAIL_COMMAND_RE.search(text))


def looks_like_direct_quickcep_cli(text: str) -> bool:
    return bool(text) and bool(_QUICKCEP_CLI_COMMAND_RE.search(text))


def looks_like_cs_bridge_tool_invocation(text: str) -> bool:
    return bool(text) and bool(_CS_BRIDGE_TOOL_COMMAND_RE.search(text))


def block_payload(*, via: str) -> dict[str, str]:
    return {
        "action": "block",
        "message": _BLOCK_MESSAGE,
        "blocked_by": "cs-bridge-agent-guard",
        "via": via,
    }


def cli_block_payload(*, reason: str = "send-email") -> dict[str, str]:
    if reason == "quickcep_cli":
        return {
            "error": _CLI_BLOCK_MESSAGE,
            "blocked_by": "cs-bridge-agent-guard",
            "hint": "Use cs_bridge_tool. Manual override: CS_OPS_ALLOW_QUICKCEP_CLI=1",
        }
    return {
        "error": _BLOCK_MESSAGE,
        "blocked_by": "cs-bridge-agent-guard",
        "hint": "Use cs_bridge_tool draft-save. Manual CLI override: CS_OPS_ALLOW_QUICKCEP_SEND=1",
    }


def should_block_cli_send_email() -> bool:
    if not guard_enabled() or manual_send_override():
        return False
    return povison_profile_active()


def assert_cli_send_allowed() -> None:
    """Call at quickcep_cli send-email entry; exits process when blocked."""
    if not should_block_cli_send_email():
        return
    print(json.dumps(cli_block_payload(reason="send-email"), ensure_ascii=False), file=sys.stderr)
    sys.exit(2)


def should_block_agent_quickcep_cli() -> bool:
    if not guard_enabled() or manual_cli_override():
        return False
    return povison_profile_active()


def pre_tool_block(
    *,
    tool_name: str,
    args: dict,
    task_id: str = "",
    session_id: str = "",
    run_kind: str = "",
) -> Optional[dict[str, str]]:
    # PR3: edit_memory whitelist — only hindsight memory tools allowed.
    if run_kind == "edit_memory":
        if tool_name in EDIT_MEMORY_ALLOWED_TOOLS:
            return None
        return {
            "action": "block",
            "message": EDIT_MEMORY_BLOCK_MESSAGE,
            "blocked_by": "cs-bridge-agent-guard",
            "via": f"edit_memory_whitelist:{tool_name}",
        }

    if not guard_enabled():
        return None
    if not cs_automation_session(session_id, task_id):
        return None

    if tool_name == "terminal":
        command = str(args.get("command") or args.get("cmd") or "")
        if looks_like_quickcep_send_email(command):
            return block_payload(via="terminal")
        if should_block_agent_quickcep_cli() and looks_like_direct_quickcep_cli(command):
            return {
                **block_payload(via="terminal"),
                "message": _CLI_BLOCK_MESSAGE,
            }
        return None

    if tool_name in ("execute_code", "code_execution"):
        code = str(args.get("code") or args.get("source") or "")
        if looks_like_quickcep_send_email(code):
            return block_payload(via=tool_name)
        if should_block_agent_quickcep_cli() and looks_like_direct_quickcep_cli(code):
            return {
                **block_payload(via=tool_name),
                "message": _CLI_BLOCK_MESSAGE,
            }
        if looks_like_cs_bridge_tool_invocation(code):
            return {
                "action": "block",
                "message": _EXECUTE_CODE_BRIDGE_BLOCK_MESSAGE,
                "blocked_by": "cs-bridge-agent-guard",
                "via": tool_name,
            }
        return None

    return None
