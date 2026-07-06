"""L2 live QuickCEP fetchers for the Console workbench (PR1.5).

These hit QuickCEP REST via ``quickcep_cli`` and are wrapped with short-lived
in-memory caches so the FE can poll without hammering the platform:

- messages: ``GET /messages?since=<message_id>`` — 15s cache on page 0 (the
  newest page); ``since`` filtering is applied to the cached page, with a
  fresh-fetch fallback when ``since`` is not found in the cached page. Older
  pages (``page > 0``) bypass the cache and are fetched on scroll-up; the FE
  dedups by message id to handle ``pageIndex`` drift. Callers that require
  the latest state (e.g. send-reply message_id backfill) pass
  ``force_fresh=True`` to bypass the cache.
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
MESSAGES_CACHE_TTL_SEC = 15

_tags_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_orders_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_messages_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _error(source: str, exc: BaseException | str) -> dict[str, Any]:
    msg = str(exc)[:300]
    log.warning("quickcep_live %s failed: %s", source, msg)
    return {"ok": False, "source": source, "error": msg}


DEFAULT_MESSAGES_PAGE_SIZE = 10


def _fetch_messages_fresh(
    *,
    quickcep_session_id: str,
    page: int = 0,
    page_size: int = DEFAULT_MESSAGES_PAGE_SIZE,
) -> dict[str, Any]:
    """Hit quickcep_cli for one message page (no cache). Best-effort.

    ``page`` is the 0-based QuickCEP ``pageIndex`` (page 0 = newest page).
    Older pages are fetched on user scroll-up; the caller is responsible for
    drift handling (id-based dedup) since ``pageIndex`` is offset-based and
    shifts when new messages arrive.
    """
    code, out, err = _run_quickcep_cli([
        "messages", quickcep_session_id,
        "--page", str(page), "--page-size", str(page_size), "--chronological",
    ])
    if code != 0:
        return _error("messages", err or out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return _error("messages", exc)
    messages = payload.get("messages") or []
    total = payload.get("total", len(messages))
    return {
        "ok": True,
        "session_id": quickcep_session_id,
        "total": total,
        "count": len(messages),
        "page": page,
        "page_size": page_size,
        # Whether older messages still exist beyond this page. The FE uses
        # this as a hint; the authoritative stop signal is `loadedCount >=
        # total` plus the "page returned 0 records" fallback.
        "has_more": (page + 1) * page_size < total,
        "messages": messages,
    }


def _apply_since_filter(payload: dict[str, Any], since: str) -> dict[str, Any]:
    """Return a copy of ``payload`` with only messages after ``since`` id.

    Best-effort: if ``since`` is not present in the cached page, return ``None``
    so the caller can decide to fall back to a fresh fetch.
    """
    messages = payload.get("messages") or []
    filtered: list[dict[str, Any]] = []
    seen_since = False
    for m in messages:
        if not seen_since:
            if str(m.get("id") or "") == str(since):
                seen_since = True
            continue
        filtered.append(m)
    if not seen_since:
        return None
    out = dict(payload)
    out["messages"] = filtered
    out["count"] = len(filtered)
    return out


def _apply_since_filter_loose(payload: dict[str, Any], since: str) -> dict[str, Any]:
    """Like ``_apply_since_filter`` but falls back to the whole page when
    ``since`` is not found (matches the pre-cache behavior: "best-effort: if
    the id is not in this page, return the whole page").
    """
    strict = _apply_since_filter(payload, since)
    if strict is not None:
        return strict
    return payload


def fetch_messages(
    *,
    quickcep_session_id: str,
    since: Optional[str] = None,
    force_fresh: bool = False,
    page: int = 0,
    page_size: int = DEFAULT_MESSAGES_PAGE_SIZE,
) -> dict[str, Any]:
    """One message page, optionally filtered to messages after ``since`` id.

    Page 0 (the newest page) is cached for ``MESSAGES_CACHE_TTL_SEC`` when
    fetched with ``since is None``. ``since`` filtering is applied to the
    cached page in-memory; when the id is absent from the cached page, the
    function falls back to a fresh CLI call so the caller never silently
    drops newer messages.

    Older pages (``page > 0``) are **never read from or written to the
    cache**: they are stable historical data, requested rarely (only on
    scroll-up), and caching them would risk serving a stale shifted page
    during drift. The FE handles drift via id-based dedup.

    ``force_fresh=True`` bypasses the read cache and refreshes it with the
    fresh result (page 0 only). Callers that need the latest state right
    after a mutation (e.g. send-reply message_id backfill) should set this.
    """
    now = time.time()
    cached = _messages_cache.get(quickcep_session_id)
    use_cached = (
        not force_fresh
        and page == 0
        and cached is not None
        and (now - cached[0]) < MESSAGES_CACHE_TTL_SEC
    )

    if use_cached and since is None:
        log.debug("messages cache hit session=%s", quickcep_session_id)
        return cached[1]

    if use_cached and since is not None:
        filtered = _apply_since_filter(cached[1], since)
        if filtered is not None:
            log.debug("messages cache hit (since) session=%s", quickcep_session_id)
            return filtered
        # since id not in cached page → page is stale relative to since; fall through.

    result = _fetch_messages_fresh(
        quickcep_session_id=quickcep_session_id, page=page, page_size=page_size,
    )
    # Only cache successful, full-page (since is None) page-0 results. Errors,
    # since-filtered views, and older pages are not stored.
    if result.get("ok") and since is None and page == 0:
        _messages_cache[quickcep_session_id] = (now, result)
    # Preserve the pre-cache "best-effort whole page when since not found"
    # behavior on fresh fetches too.
    if since is not None and result.get("ok"):
        return _apply_since_filter_loose(result, since)
    return result


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
    """Drop cached tags/orders/messages (e.g. after a note add, send-reply, or
    inbound watcher event that may bump the session)."""
    if quickcep_session_id:
        _tags_cache.pop(quickcep_session_id, None)
        _orders_cache.pop(quickcep_session_id, None)
        _messages_cache.pop(quickcep_session_id, None)
    else:
        _tags_cache.clear()
        _orders_cache.clear()
        _messages_cache.clear()
