"""Hermes pre_tool_call guard — enforce kol_bridge_tool agent contract."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

HookResult = Optional[Union[None, Dict[str, str]]]

# NOTE: use bare ``kol-campaign`` (no colon) so it also matches the
# ``kol-campaign-draft:`` and ``kol-campaign-outreach:`` task ids the Console
# emits — a trailing colon here let those slip past the mcp-chrome block and
# loop on the dead chrome-devtools MCP endpoint (POVISON stuck-run incident).
_KOL_SESSION_PREFIXES = ("kol-campaign", "kol-reply:", "kol-email-discover:", "kol-nox-")

# Post-approval / reply / Nox batch: Nox API + bridge CLI only — no browser crawl.
# ``kol-email-discover:`` is intentionally excluded: Console「全网搜索邮箱」runs
# ``kol-email-discovery`` Tier 2 with built-in ``browser_*`` (never MCP Chrome).
_BROWSER_BLOCKED_SESSION_PREFIXES = (
    "kol-campaign-outreach:",
    "kol-campaign-draft:",
    "kol-nox-contacts-batch:",
    "kol-reply:",
)

_BROWSER_TOOL_PREFIX = "browser_"
_MCP_CHROME_TOOL_PREFIX = "mcp_chrome_devtools_"


def _guard_enabled() -> bool:
    return os.environ.get("KOL_BRIDGE_AGENT_GUARD", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _session_key(session_id: str, task_id: str = "") -> str:
    """Gateway runs pass ``task_id`` (e.g. kol-email-discover:LIVE:701); hooks
    historically received empty ``session_id``. Prefer whichever is set."""
    return (session_id or task_id or "").strip()


def _kol_session(session_id: str, task_id: str = "") -> bool:
    sid = _session_key(session_id, task_id)
    return any(sid.startswith(p) for p in _KOL_SESSION_PREFIXES)


def _browser_blocked_session(session_id: str, task_id: str = "") -> bool:
    sid = _session_key(session_id, task_id)
    return any(sid.startswith(p) for p in _BROWSER_BLOCKED_SESSION_PREFIXES)


def _is_browser_tool(tool_name: str) -> bool:
    return tool_name.startswith(_BROWSER_TOOL_PREFIX)


def _is_mcp_chrome_tool(tool_name: str) -> bool:
    return tool_name.startswith(_MCP_CHROME_TOOL_PREFIX)


def _load_contract():
    cached = sys.modules.get("kol_ops_bridge_bridge_agent_contract_guard")
    if cached is not None:
        return cached
    path = (
        Path(__file__).resolve().parents[1]
        / "kol-ops-bridge"
        / "bridge_agent_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_bridge_agent_contract_guard",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load bridge_agent_contract from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _extract_text(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "execute_code":
        return str(args.get("code") or args.get("source") or "")
    if tool_name == "terminal":
        return str(args.get("command") or args.get("cmd") or "")
    return ""


def _extract_path(tool_name: str, args: Dict[str, Any]) -> str:
    for key in ("path", "file_path", "target_file", "query"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


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

    sid = _session_key(session_id, task_id)

    if _is_mcp_chrome_tool(tool_name) and _kol_session(session_id, task_id):
        return {
            "action": "block",
            "message": (
                "mcp_chrome_devtools_* is disabled for all KOL gateway sessions "
                f"({sid or 'kol-*'}). The remote CDP endpoint is unreliable; use "
                "built-in browser_* (discovery / kol-email-discovery Tier 2 only) "
                "or kol-bridge-cli / nox_kol_tool.py for email enrichment. "
                "Do not fall back to MCP after a connection error."
            ),
        }

    if _is_browser_tool(tool_name) and _browser_blocked_session(session_id, task_id):
        return {
            "action": "block",
            "message": (
                "Browser tools are disabled for post-approval outreach, reply dispatch, "
                "and Nox contact batch sessions. Use kol-bridge-cli / nox_kol_tool.py "
                "(Nox contacts --gate pre_outreach_confirm when nox_quota_enabled). "
                "Do not use browser_* for email lookup on outreach/reply runs."
            ),
        }

    try:
        contract = _load_contract()
    except Exception as exc:
        logger.warning("kol-bridge-agent-guard: contract load failed: %s", exc)
        return None

    if tool_name in ("execute_code", "terminal"):
        text = _extract_text(tool_name, args)
        violations = contract.lint_agent_bridge_snippet(text)
        if violations:
            return {
                "action": "block",
                "message": contract.format_block_message(violations),
            }
        return None

    if tool_name in ("read_file", "search_files", "grep", "glob_file_search"):
        if not _kol_session(session_id, task_id):
            return None
        path = _extract_path(tool_name, args)
        norm = path.replace("\\", "/").lower()
        if norm.endswith(".env") or "/.env" in norm:
            return {
                "action": "block",
                "message": contract.format_block_message([{
                    "code": "read_env_file",
                    "hint": "Do not read .env for bridge keys; kol_bridge_tool inherits HERMES_KOL_OPS_BRIDGE_KEY.",
                    "canonical_cli": contract.CANONICAL_CLI_REL,
                }]),
            }
        violations = contract.lint_file_tool_path(path)
        if "kol-ops-bridge" in path.replace("\\", "/").lower() and tool_name == "search_files":
            violations = violations or [{
                "code": "search_bridge_tree",
                "hint": (
                    "Do not search kol-ops-bridge source. Use print-agent-contract, "
                    "skill_view(bridge-http-api-endpoints), or kol_bridge_tool.py --help."
                ),
                "canonical_cli": contract.CANONICAL_CLI_REL,
            }]
        if violations:
            return {
                "action": "block",
                "message": contract.format_block_message(violations),
            }

    return None
