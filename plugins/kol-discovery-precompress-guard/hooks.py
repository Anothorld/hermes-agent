"""``pre_compress`` hook implementation for KOL discovery sessions.

When context compression is about to discard conversation history on a
``kol-campaign:LIVE:*`` session, walk the messages for
``browser_navigate`` calls to ``instagram.com/<handle>/`` profiles and
snapshot handles that were visited but NOT yet ingested via
``ingest-confirmed-candidate``. The snapshot is written to
``/tmp/precompress_pending_<session>.json`` so the next rediscover round's
``# resume_directives`` STEP_0 can recover them.

This is a side-effect-only hook: it never blocks compression and never
raises into the caller (all failures are logged and swallowed).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# kol-campaign:LIVE:SSF8033-20260609  (NOT kol-campaign-outreach / kol-campaign-draft)
_DISCOVERY_SESSION_RE = re.compile(r"^kol-campaign:(?:LIVE|TEST):")

# instagram.com/<handle>/  — capture the handle, skip /explore, /reel, /p, /accounts
_IG_PROFILE_URL_RE = re.compile(
    r"instagram\.com/(?!explore/|reel/|p/|accounts/|direct/|stories/)"
    r"([A-Za-z0-9._]{1,30})/?",
)

# Terminal tool output for a successful ingest mentions the CLI subcommand
# A successful ``ingest-confirmed-candidate`` bridge response is JSON
# containing a numeric ``candidate_id`` (and usually ``identity_id``).
# Error responses (HTTP 400/422) carry ``"error":...`` and ``"status":4xx``
# but NO numeric ``candidate_id`` — so requiring ``"candidate_id":\s*\d+``
# avoids false-positiving on failed-ingest error text that happens to
# mention ``identity_id`` in a validation message (e.g.
# ``{"detail":"identity_id required"}``).
_INGEST_SUCCESS_RE = re.compile(
    r'"candidate_id"\s*:\s*\d+',
    re.IGNORECASE,
)


def _is_discovery_session(session_id: str, task_id: str = "") -> bool:
    """True for ``kol-campaign:LIVE:*`` / ``kol-campaign:TEST:*`` sessions
    (launch + rediscover), False for outreach / draft / reply sessions.

    Mirrors ``kol-bridge-agent-guard``'s ``_campaign_discovery_session``
    without cross-plugin importing: ``sid = (session_id or task_id or
    "").strip()``, starts with ``kol-campaign``, and NOT in the
    browser-blocked set (outreach / draft).
    """
    sid = (session_id or task_id or "").strip()
    if not sid:
        return False
    # Exclude the sibling session prefixes that also start with kol-campaign.
    if sid.startswith("kol-campaign-outreach"):
        return False
    if sid.startswith("kol-campaign-draft"):
        return False
    if sid.startswith("kol-campaign-reply"):
        return False
    return bool(_DISCOVERY_SESSION_RE.match(sid))


def _extract_handle_from_url(url: str) -> Optional[str]:
    """Return the IG handle for a profile URL, or None for non-profile URLs."""
    if not url:
        return None
    m = _IG_PROFILE_URL_RE.search(url)
    if not m:
        return None
    handle = m.group(1)
    # Filter obvious non-handle path segments.
    if handle.lower() in {"www", "instagram", "explore"}:
        return None
    return handle.lower()


def _iter_tool_calls(messages: Iterable[Dict[str, Any]]) -> Iterable[Tuple[str, str]]:
    """Yield ``(tool_name, arguments_str)`` for every assistant tool_call
    across the message list. ``arguments_str`` is the raw JSON string the
    model emitted (parse defensively)."""
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            # OpenAI shape: {"id":..., "type":"function",
            #                 "function": {"name":..., "arguments": "..."}}
            fn = tc.get("function") or {}
            name = fn.get("name") or tc.get("name") or ""
            args = fn.get("arguments") or tc.get("arguments") or ""
            if isinstance(args, dict):
                try:
                    args = json.dumps(args)
                except (TypeError, ValueError):
                    args = str(args)
            if name:
                yield name, str(args)


def _iter_tool_results(messages: Iterable[Dict[str, Any]]) -> Iterable[str]:
    """Yield the textual content of every tool result message."""
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if content is None:
            continue
        if isinstance(content, list):
            # OpenAI multi-part tool result
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
                else:
                    parts.append(str(part))
            yield "\n".join(parts)
        else:
            yield str(content)


def collect_visited_handles(messages: List[Dict[str, Any]]) -> List[str]:
    """Return the ordered, deduplicated list of IG handles visited via
    ``browser_navigate`` in this message history."""
    seen: List[str] = []
    seen_set: set[str] = set()
    for name, args in _iter_tool_calls(messages):
        if name != "browser_navigate":
            continue
        url = _parse_url_from_arguments(args)
        if not url:
            continue
        handle = _extract_handle_from_url(url)
        if handle and handle not in seen_set:
            seen.append(handle)
            seen_set.add(handle)
    return seen


def collect_ingested_handles(messages: List[Dict[str, Any]]) -> set[str]:
    """Return the set of handles that appear in a successful
    ``ingest-confirmed-candidate`` terminal output.

    Heuristic: a tool result whose text matches the ingest-success pattern
    AND contains an IG handle-shaped token. We do NOT require the handle to
    also appear in a navigate call here — the caller intersects with
    visited handles.
    """
    ingested: set[str] = set()
    for text in _iter_tool_results(messages):
        if not _INGEST_SUCCESS_RE.search(text):
            continue
        # The ingest CLI command line (echoed in the result or a nearby
        # assistant message) usually carries --json @/tmp/ingest_<handle>.json
        for m in re.finditer(r"ingest_([A-Za-z0-9._]+)\.json", text):
            ingested.add(m.group(1).lower())
        # Also catch handles mentioned directly in the success payload.
        for m in re.finditer(r'"primary_handle"\s*:\s*"([A-Za-z0-9._]+)"', text):
            ingested.add(m.group(1).lower())
    return ingested


def _parse_url_from_arguments(arguments_str: str) -> Optional[str]:
    """Best-effort parse of the ``url`` field from a tool-call arguments
    JSON string. Returns None on any parse failure."""
    if not arguments_str:
        return None
    try:
        parsed = json.loads(arguments_str)
    except (TypeError, ValueError):
        # Fall back to a regex scrape for resilient shape changes.
        m = re.search(r'"url"\s*:\s*"([^"]+)"', arguments_str)
        return m.group(1) if m else None
    if isinstance(parsed, dict):
        url = parsed.get("url")
        if isinstance(url, str):
            return url
    return None


def compute_pending_handles(
    messages: List[Dict[str, Any]],
) -> List[str]:
    """Pure helper: visited − ingested, preserving visit order.

    Exported for unit tests so the message-walking logic can be verified
    without touching the filesystem.
    """
    visited = collect_visited_handles(messages)
    if not visited:
        return []
    ingested = collect_ingested_handles(messages)
    return [h for h in visited if h not in ingested]


def _snapshot_path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")
    return os.path.join(tempfile.gettempdir(), f"precompress_pending_{safe}.json")


def _write_snapshot(path: str, session_id: str, pending: List[str]) -> None:
    payload = {
        "session_id": session_id,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pending_handles": pending,
        "count": len(pending),
        "note": (
            "Handles visited via browser_navigate to instagram.com/<handle>/ "
            "in the compressed-away context but NOT yet ingested via "
            "ingest-confirmed-candidate. The next rediscover round's "
            "# resume_directives STEP_0 should re-process these before "
            "opening new browser_navigate calls."
        ),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def pre_compress(
    session_id: str = "",
    task_id: str = "",
    messages: Optional[List[Dict[str, Any]]] = None,
    **_: Any,
) -> None:
    """``pre_compress`` lifecycle hook entry point.

    Side-effect only: writes ``/tmp/precompress_pending_<session>.json``
    and logs the cleanup manifest. Never raises — compression must not be
    blocked by this hook.
    """
    try:
        if not _is_discovery_session(session_id, task_id):
            return None
        pending = compute_pending_handles(messages or [])
        if not pending:
            logger.info(
                "precompress-guard: %s — no visited-but-unconfirmed handles "
                "to snapshot.",
                session_id,
            )
            return None
        path = _snapshot_path(session_id)
        _write_snapshot(path, session_id, pending)
        logger.warning(
            "precompress-guard: %s — compression is about to discard "
            "%d visited-but-unconfirmed handle(s). Snapshot written to %s. "
            "Manifest: %s",
            session_id, len(pending), path, ", ".join(pending),
        )
        return None
    except Exception:
        # NEVER propagate — compression must proceed regardless of snapshot
        # failure. The next round's visited_handles / pending_ingests in the
        # agent's own structured diagnostics is the durable fallback.
        logger.exception("precompress-guard: failed to snapshot pending handles")
        return None
