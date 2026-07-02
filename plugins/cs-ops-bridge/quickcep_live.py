"""L2 live QuickCEP fetchers for the Console workbench (PR1.5).

These hit QuickCEP REST via ``quickcep_cli`` and are wrapped with short-lived
in-memory caches so the FE can poll without hammering the platform:

- messages: ``GET /messages?since=<message_id>`` — incremental, no cache (must be fresh)
- tags:     ``GET /tags`` — 300s cache (tagIds reverse-resolved to names via tag map)
- orders:   ``GET /orders`` — 60s cache (reuses cal._fetch_visitor_orders)
- note:     ``POST /note`` — reuses session_handoff.apply_quickcep_note (no cache)

All fetchers are best-effort: on any QuickCEP failure they return an ``error``
payload rather than raising, so the Console degrades gracefully.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from . import cal
from .session_handoff import _run_quickcep_cli, load_tag_map

log = logging.getLogger(__name__)

TAGS_CACHE_TTL_SEC = 300
ORDERS_CACHE_TTL_SEC = 60

_tags_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_orders_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _error(source: str, exc: BaseException | str) -> dict[str, Any]:
    msg = str(exc)[:300]
    log.warning("quickcep_live %s failed: %s", source, msg)
    return {"ok": False, "source": source, "error": msg}


def fetch_messages(*, quickcep_session_id: str, since: Optional[str] = None) -> dict[str, Any]:
    """Latest message page, optionally filtered to messages after ``since`` id."""
    code, out, err = _run_quickcep_cli([
        "messages", quickcep_session_id, "--page", "0", "--page-size", "50", "--chronological",
    ])
    if code != 0:
        return _error("messages", err or out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return _error("messages", exc)
    messages = payload.get("messages") or []
    if since:
        # Drop everything up to and including the since id (best-effort: if the
        # id is not in this page, return the whole page).
        filtered: list[dict[str, Any]] = []
        seen_since = False
        for m in messages:
            if not seen_since:
                if str(m.get("id") or "") == str(since):
                    seen_since = True
                continue
            filtered.append(m)
        messages = filtered if seen_since else messages
    return {
        "ok": True,
        "session_id": quickcep_session_id,
        "total": payload.get("total", len(messages)),
        "count": len(messages),
        "messages": messages,
    }


def _resolve_tag_names(tag_ids: list[str]) -> list[dict[str, Any]]:
    """Map QuickCEP tag IDs to {id, name} using the session tag map (best-effort)."""
    tag_map = load_tag_map()
    # Build id -> name index across known sections of the yaml.
    id_to_name: dict[str, str] = {}
    for section in ("ai_lifecycle", "business", "inquiry"):
        node = tag_map.get(section) or {}
        if isinstance(node, dict):
            for name, tid in node.items():
                if tid:
                    id_to_name[str(tid)] = str(name)
    out: list[dict[str, Any]] = []
    for tid in tag_ids:
        out.append({"id": str(tid), "name": id_to_name.get(str(tid))})
    return out


def fetch_session_tags(*, quickcep_session_id: str) -> dict[str, Any]:
    """Session tag IDs (reverse-resolved to names), cached for TAGS_CACHE_TTL_SEC."""
    now = time.time()
    cached = _tags_cache.get(quickcep_session_id)
    if cached and (now - cached[0]) < TAGS_CACHE_TTL_SEC:
        return cached[1]
    code, out, err = _run_quickcep_cli(["tags-get", quickcep_session_id])
    if code != 0:
        return _error("tags", err or out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return _error("tags", exc)
    tag_ids = [str(t) for t in (payload.get("tagIds") or [])]
    result = {
        "ok": True,
        "session_id": quickcep_session_id,
        "tagIds": tag_ids,
        "tags": _resolve_tag_names(tag_ids),
    }
    _tags_cache[quickcep_session_id] = (now, result)
    return result


def fetch_session_orders(*, quickcep_session_id: str, env: str = "LIVE") -> dict[str, Any]:
    """Customer orders + intention_tags, cached for ORDERS_CACHE_TTL_SEC."""
    now = time.time()
    cached = _orders_cache.get(quickcep_session_id)
    if cached and (now - cached[0]) < ORDERS_CACHE_TTL_SEC:
        return cached[1]
    try:
        ctx = cal._fetch_visitor_orders(quickcep_session_id, env=env)
    except Exception as exc:  # noqa: BLE001 — best-effort
        return _error("orders", exc)
    result = {
        "ok": True,
        "session_id": quickcep_session_id,
        "orders": ctx.get("orders") or [],
        "intention_tags": ctx.get("intention_tags") or [],
        "source": ctx.get("source"),
    }
    if ctx.get("error"):
        result["warning"] = ctx["error"]
    _orders_cache[quickcep_session_id] = (now, result)
    return result


def add_note(
    *,
    quickcep_session_id: str,
    chat_session_id: str,
    text: str,
) -> dict[str, Any]:
    """Add a QuickCEP internal note (reuses session_handoff.apply_quickcep_note)."""
    from .session_handoff import apply_quickcep_note

    return apply_quickcep_note(
        quickcep_session_id=quickcep_session_id,
        chat_session_id=chat_session_id,
        note_body=text,
    )


def invalidate_cache(quickcep_session_id: Optional[str] = None) -> None:
    """Drop cached tags/orders (e.g. after a note add that may bump session)."""
    if quickcep_session_id:
        _tags_cache.pop(quickcep_session_id, None)
        _orders_cache.pop(quickcep_session_id, None)
    else:
        _tags_cache.clear()
        _orders_cache.clear()
