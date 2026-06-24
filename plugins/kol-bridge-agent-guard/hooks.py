"""Hermes pre_tool_call guard — enforce kol_bridge_tool agent contract."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

HookResult = Optional[Union[None, Dict[str, str]]]

# NOTE: use bare ``kol-campaign`` (no colon) so it also matches the
# ``kol-campaign-draft:`` and ``kol-campaign-outreach:`` task ids the Console
# emits — a trailing colon here let those slip past the mcp-chrome block and
# loop on the dead chrome-devtools MCP endpoint (POVISON stuck-run incident).
_KOL_SESSION_PREFIXES = (
    "kol-campaign",
    "kol-reply:",
    "kol-email-discover:",
    "kol-creator-brief-refresh:",
    "kol-nox-",
)

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
_VEEDCRAWL_TOOL_PREFIX = "veedcrawl_"

# ``execute_code`` / ``terminal`` workarounds that bypass Tier-2 ``browser_*``
# (POVISON 701: model spawned delegate_task, imported hermes_tools in
# execute_code, and curled DuckDuckGo HTML instead of browser_navigate).
_EXEC_CODE_BROWSER_WORKAROUND_RE = re.compile(
    r"(?is)"
    r"hermes_tools|"
    r"\bbrowser_(?:navigate|snapshot|click|get_images)\b|"
    r"\bplaywright\b|\bselenium\b|\bpyppeteer\b",
)
_TERMINAL_SCRAPE_WORKAROUND_RE = re.compile(
    r"(?is)"
    # search-engine / link-in-bio HTML scraping
    r"duckduckgo\.com|google\.com/search|bing\.com/search|"
    r"serper\.dev|google\.serper|"
    r"beacons\.ai|linktr\.ee|bio\.link|lnk\.bio|solo\.to|instagram\.com|"
    # generic HTTP-fetch-from-terminal substitutes for web_extract / browser_*
    r"\bcurl\b|\bwget\b|"
    r"urllib\.request|urlopen\b|\brequests\.(?:get|post)\b|"
    r"httpx\.|http\.client",
)

# Public-web workarounds on kol-campaign discovery — narrower than email-discover
# terminal scrape (do not block bridge-health curl in execute_code).
_CAMPAIGN_PUBLIC_WEB_WORKAROUND_RE = re.compile(
    r"(?is)"
    r"serper\.dev|google\.serper|"
    r"duckduckgo\.com|google\.com/search|bing\.com/search|"
    r"\brequests\.(?:get|post)\b|"
    r"urllib\.request|urlopen\b|httpx\.|http\.client|"
    r"\bcurl\b.*(?:serper|google\.com/search|duckduckgo|bing\.com)|"
    r"\bwget\b",
)


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


def _is_veedcrawl_tool(tool_name: str) -> bool:
    return tool_name.startswith(_VEEDCRAWL_TOOL_PREFIX)


def _email_discover_session(session_id: str, task_id: str = "") -> bool:
    sid = _session_key(session_id, task_id)
    return sid.startswith("kol-email-discover:")


def _creator_brief_refresh_session(session_id: str, task_id: str = "") -> bool:
    sid = _session_key(session_id, task_id)
    return sid.startswith("kol-creator-brief-refresh:")


def _browser_enrichment_session(session_id: str, task_id: str = "") -> bool:
    """Email discover and dedicated creator-brief refresh runs."""
    return (
        _email_discover_session(session_id, task_id)
        or _creator_brief_refresh_session(session_id, task_id)
    )


def _campaign_discovery_session(session_id: str, task_id: str = "") -> bool:
    """Launch / rediscover runs (``kol-campaign:LIVE:...``), not outreach/draft."""
    sid = _session_key(session_id, task_id)
    if not sid.startswith("kol-campaign"):
        return False
    return not _browser_blocked_session(session_id, task_id)


def _email_discover_workaround_message(kind: str) -> str:
    return (
        f"{kind} is disabled for kol-email-discover runs. Tier 1 Google search "
        "and all page fetches use local debug Chrome: `browser_navigate` to "
        "`https://www.google.com/search?q=...` (URL-encode the query), then "
        "`browser_snapshot`; open result URLs with the same tools. Tier 2 "
        "(JS-gated: Instagram, Linktree, Beacons): `browser_navigate` + "
        "`browser_snapshot`. Do NOT use `web_search`, `web_extract`, terminal "
        "curl/urllib/requests HTTP, veedcrawl_*, delegate_task, execute_code "
        "browser imports, or mcp_chrome_devtools_* as substitutes."
    )


def _nox_tool_invocation() -> str:
    try:
        contract = _load_contract()
        return str(getattr(contract, "NOX_TOOL_INVOCATION", "")).strip() or (
            "python3 plugins/nox-kol-bridge/scripts/nox_kol_tool.py"
        )
    except Exception:
        return "python3 plugins/nox-kol-bridge/scripts/nox_kol_tool.py"


def _browser_blocked_message() -> str:
    nox = _nox_tool_invocation()
    return (
        "Browser tools are disabled for post-approval outreach, reply dispatch, "
        "and Nox contact batch sessions. Use kol_bridge_tool.py or "
        f"{nox} (Nox contacts --gate pre_outreach_confirm when nox_quota_enabled). "
        "Do not use browser_* for email lookup on outreach/reply/draft runs."
    )


def _campaign_discovery_public_web_message(kind: str) -> str:
    return (
        f"{kind} is disabled for kol-campaign discovery runs. Public web "
        "(Google / TikTok / Reddit) uses local debug Chrome: "
        "`browser_navigate` to "
        "`https://www.google.com/search?q=<url_encoded_query>`, then "
        "`browser_snapshot`; open promising result URLs with the same tools. "
        "Cross-verify IG handles via "
        "`browser_navigate(https://www.instagram.com/<handle>/)` and read "
        "`ig_readiness` / `ig_followers_hint`. Do NOT use `web_search`, "
        "`web_extract`, terminal curl/urllib/requests, Serper API one-liners, "
        "or `python3 -c` HTTP scripts as substitutes."
    )


def _campaign_discovery_workaround_message(kind: str) -> str:
    return (
        f"{kind} is disabled for kol-campaign discovery runs. Execute Instagram "
        "discovery in THIS run with local debug Chrome (`browser_navigate`, "
        "`browser_snapshot`, `browser_click`, `browser_console`, "
        "`browser_get_images`, `vision_analyze`) per "
        "`instagram-kol-discovery` SKILL.md. Persist each qualified handle "
        "via kol_bridge_tool.py before moving on. For quantity shortfalls, "
        "finish with `floor_unmet_reason` + `attempted_angles` and let the "
        "console auto-fire `/rediscover` — do NOT spawn subagents. "
        "Veedcrawl calls must include full args (e.g. "
        '`{"q": "luxury home decor", "platform": "instagram"}` for '
        "veedcrawl_search_social_videos). Do NOT use delegate_task, "
        "mcp_chrome_devtools_*, or terminal HTTP scraping as substitutes. "
        "For Google public-web discovery, see "
        "`instagram-kol-discovery` → Public web via browser Google."
    )


def _load_discovery_session():
    cached = sys.modules.get("kol_bridge_agent_guard_discovery_session")
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "internal" / "discovery_session.py"
    spec = importlib.util.spec_from_file_location(
        "kol_bridge_agent_guard_discovery_session",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load discovery_session from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def _discovery_exploration_tool(tool_name: str) -> bool:
    return _is_browser_tool(tool_name) or _is_veedcrawl_tool(tool_name)


def _check_discovery_bootstrap(
    tool_name: str,
    args: Dict[str, Any],
    session_id: str,
    task_id: str,
) -> HookResult:
    """Block exploration tools until list-candidates + exclusion pulls complete."""
    ds = _load_discovery_session()
    sid = _session_key(session_id, task_id)
    if not _campaign_discovery_session(session_id, task_id):
        return None

    if tool_name == "terminal":
        command = _extract_text(tool_name, args)
        binding_err = ds.validate_campaign_binding(sid, command)
        if binding_err:
            return {"action": "block", "message": binding_err}
        step = ds.classify_bootstrap_step(command)
        if step:
            ds.mark_bootstrap_step(sid, step)
        return None

    if _discovery_exploration_tool(tool_name) and not ds.bootstrap_complete(sid):
        return {
            "action": "block",
            "message": ds.bootstrap_block_message(sid),
        }
    return None


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

    bootstrap_result = _check_discovery_bootstrap(tool_name, args, session_id, task_id)
    if bootstrap_result is not None:
        return bootstrap_result

    if _is_mcp_chrome_tool(tool_name) and _kol_session(session_id, task_id):
        return {
            "action": "block",
            "message": (
                "mcp_chrome_devtools_* is disabled for all KOL gateway sessions "
                f"({sid or 'kol-*'}). The remote CDP endpoint is unreliable; use "
                "built-in browser_* (discovery / kol-email-discovery Tier 2 only) "
                f"or {_nox_tool_invocation()} for email enrichment. "
                "Do not fall back to MCP after a connection error."
            ),
        }

    if _campaign_discovery_session(session_id, task_id):
        if tool_name == "delegate_task":
            return {
                "action": "block",
                "message": _campaign_discovery_workaround_message("delegate_task"),
            }
        if tool_name in ("web_search", "web_extract"):
            return {
                "action": "block",
                "message": _campaign_discovery_public_web_message(tool_name),
            }
        if tool_name == "terminal":
            cmd = _extract_text(tool_name, args)
            if _CAMPAIGN_PUBLIC_WEB_WORKAROUND_RE.search(cmd):
                return {
                    "action": "block",
                    "message": _campaign_discovery_public_web_message("terminal HTTP"),
                }
        if tool_name == "execute_code":
            code = _extract_text(tool_name, args)
            if _CAMPAIGN_PUBLIC_WEB_WORKAROUND_RE.search(code):
                return {
                    "action": "block",
                    "message": _campaign_discovery_public_web_message("execute_code HTTP"),
                }

    if _browser_enrichment_session(session_id, task_id):
        if tool_name in ("web_search", "web_extract"):
            return {
                "action": "block",
                "message": _email_discover_workaround_message(tool_name),
            }
        if _is_veedcrawl_tool(tool_name):
            return {
                "action": "block",
                "message": _email_discover_workaround_message(tool_name),
            }
        if tool_name == "delegate_task":
            return {
                "action": "block",
                "message": _email_discover_workaround_message("delegate_task"),
            }
        if tool_name == "execute_code":
            code = _extract_text(tool_name, args)
            if _EXEC_CODE_BROWSER_WORKAROUND_RE.search(code):
                return {
                    "action": "block",
                    "message": _email_discover_workaround_message("execute_code"),
                }
        if tool_name == "terminal":
            cmd = _extract_text(tool_name, args)
            if _TERMINAL_SCRAPE_WORKAROUND_RE.search(cmd):
                return {
                    "action": "block",
                    "message": _email_discover_workaround_message("terminal scrape"),
                }

    if _is_browser_tool(tool_name) and _browser_blocked_session(session_id, task_id):
        return {
            "action": "block",
            "message": _browser_blocked_message(),
        }

    try:
        contract = _load_contract()
    except Exception as exc:
        logger.warning("kol-bridge-agent-guard: contract load failed: %s", exc)
        return None

    if tool_name in ("execute_code", "terminal"):
        text = _extract_text(tool_name, args)
        if _campaign_discovery_session(session_id, task_id):
            ds = _load_discovery_session()
            binding_err = ds.validate_campaign_binding(sid, text)
            if binding_err:
                return {"action": "block", "message": binding_err}
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
