"""Enrich shortlist rows with OG previews (incl. candidates without identity_id)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .bridge_client import BridgeClient, BridgeError
from .kol_profile_url import PROFILE_URL_FACT_KEYS, list_social_links_for_candidate
from .link_preview import fetch_link_preview
from .profile_og_cache import (
    OG_WRITE_SOURCE,
    facts_from_link_preview,
    link_preview_from_facts,
    normalize_profile_url,
)

log = logging.getLogger("kol_ops_console.shortlist_profile_og")

# Cap unique URL OG fetches per shortlist refresh.
_MAX_OG_URL_FETCH = 24
_OG_FETCH_CONCURRENCY = 4


async def _fetch_one(url: str) -> dict[str, Any]:
    return await fetch_link_preview(url)


async def _fetch_og_batch(urls: list[str]) -> dict[str, dict[str, Any]]:
    if not urls:
        return {}
    sem = asyncio.Semaphore(_OG_FETCH_CONCURRENCY)

    async def _run(u: str) -> tuple[str, dict[str, Any]]:
        async with sem:
            return u, await _fetch_one(u)

    pairs = await asyncio.gather(*[_run(u) for u in urls], return_exceptions=True)
    out: dict[str, dict[str, Any]] = {}
    for item in pairs:
        if isinstance(item, BaseException):
            log.warning("shortlist_og_fetch_error err=%s", item)
            continue
        url, preview = item
        out[url] = preview
    return out


async def persist_profile_og_cache(
    bridge: BridgeClient,
    *,
    identity_id: int,
    env: str,
    profile_url: str,
    preview: dict[str, Any],
) -> None:
    """Write OG snapshot to identity-scoped CAL facts."""
    if not preview.get("ok"):
        return
    facts = facts_from_link_preview(profile_url, preview)
    if len(facts) < 2:
        return
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": facts,
                "source": OG_WRITE_SOURCE,
                "env": env,
                "campaign_id": None,
            },
        )
    except BridgeError as exc:
        log.warning(
            "profile_og_cache_write_failed identity_id=%s err=%s",
            identity_id,
            exc,
        )


async def enrich_shortlist_identity_profile_urls(
    bridge: BridgeClient,
    candidates: list[dict[str, Any]],
    *,
    campaign_id: str,
    env: str,
) -> None:
    """Merge identity profile URL facts the same way KOL detail ``/facts`` does.

    ``batch_facts_subset`` can miss keys when the batch list is large or stale;
    ``read_facts`` is the operator-facing source of truth for social quick links.
    """
    sem = asyncio.Semaphore(8)

    async def _one(c: dict[str, Any]) -> None:
        iid = c.get("identity_id")
        if not isinstance(iid, int):
            return
        async with sem:
            try:
                resp = await bridge.read_facts(
                    iid, campaign_id=campaign_id, env=env
                )
            except BridgeError:
                return
        raw = resp.get("facts") if isinstance(resp, dict) else {}
        if not isinstance(raw, dict):
            return
        merged = dict(c.get("preview_facts") or {})
        for key in PROFILE_URL_FACT_KEYS:
            if key in raw and raw[key] is not None:
                merged[key] = raw[key]
        c["preview_facts"] = merged

    await asyncio.gather(*[_one(c) for c in candidates])


def _attach_social_links(candidates: list[dict[str, Any]]) -> None:
    for c in candidates:
        facts = c.get("preview_facts") if isinstance(c.get("preview_facts"), dict) else {}
        links = list_social_links_for_candidate(
            facts=facts,
            platform=c.get("platform") if isinstance(c.get("platform"), str) else None,
            handle=c.get("handle") if isinstance(c.get("handle"), str) else None,
            profile_url=c.get("profile_url") if isinstance(c.get("profile_url"), str) else None,
        )
        c["social_links"] = links
        if links and not c.get("profile_url"):
            c["profile_url"] = links[0]["url"]


def _primary_profile_url(c: dict[str, Any]) -> str | None:
    url = c.get("profile_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    links = c.get("social_links")
    if isinstance(links, list) and links:
        first = links[0]
        if isinstance(first, dict) and isinstance(first.get("url"), str):
            return first["url"]
    return None


def attach_cached_link_previews(candidates: list[dict[str, Any]]) -> None:
    """Attach ``social_links`` + cached ``link_previews`` from facts only (no HTTP)."""
    _attach_social_links(candidates)
    for c in candidates:
        facts = c.get("preview_facts") if isinstance(c.get("preview_facts"), dict) else {}
        previews: dict[str, Any] = {}
        for link in c.get("social_links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            cached = link_preview_from_facts(facts, url.strip())
            if cached:
                previews[url.strip()] = cached
        if previews:
            c["link_previews"] = previews
    _sync_legacy_link_preview(candidates)


async def enrich_shortlist_profile_og(
    bridge: BridgeClient,
    candidates: list[dict[str, Any]],
    *,
    campaign_id: str,
    env: str,
    max_url_fetch: int = _MAX_OG_URL_FETCH,
    prefetch_missing_og: bool = False,
) -> None:
    """Attach ``social_links`` + per-URL ``link_previews``.

    Default (``prefetch_missing_og=False``): CAL cache only — fast path for
    shortlist open. Hover cards fetch missing OG via ``GET /link-preview``.

    When ``prefetch_missing_og=True`` (manual Refresh): per-identity
    ``read_facts`` + live OG fetches + CAL persist (legacy slow path).
    """
    if not prefetch_missing_og:
        attach_cached_link_previews(candidates)
        return

    await enrich_shortlist_identity_profile_urls(
        bridge, candidates, campaign_id=campaign_id, env=env
    )
    _attach_social_links(candidates)

    url_jobs: list[tuple[dict[str, Any], str, int]] = []
    for c in candidates:
        facts = c.get("preview_facts") if isinstance(c.get("preview_facts"), dict) else {}
        previews: dict[str, Any] = {}
        for link in c.get("social_links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            cached = link_preview_from_facts(facts, url)
            if cached:
                previews[url] = cached
                continue
            no_id = 0 if c.get("identity_id") is None else 1
            pending = (
                0
                if c.get("candidate_status") != "selected_for_outreach"
                else 1
            )
            url_jobs.append((c, url.strip(), no_id * 10 + pending))
        if previews:
            c["link_previews"] = previews

    if not url_jobs:
        _sync_legacy_link_preview(candidates)
        return

    url_jobs.sort(key=lambda x: x[2])
    seen_urls: set[str] = set()
    fetch_urls: list[str] = []
    for _c, url, _prio in url_jobs:
        norm = normalize_profile_url(url)
        if norm in seen_urls:
            continue
        seen_urls.add(norm)
        fetch_urls.append(url)
        if len(fetch_urls) >= max_url_fetch:
            break

    fetched = await _fetch_og_batch(fetch_urls)

    for c in candidates:
        facts = c.get("preview_facts") if isinstance(c.get("preview_facts"), dict) else {}
        previews = dict(c.get("link_previews") or {}) if isinstance(c.get("link_previews"), dict) else {}
        for link in c.get("social_links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str):
                continue
            if url in previews:
                continue
            hit = fetched.get(url)
            if isinstance(hit, dict):
                previews[url] = hit
        if previews:
            c["link_previews"] = previews

        primary = _primary_profile_url(c)
        if primary and primary in previews:
            c["link_preview"] = previews[primary]
            iid = c.get("identity_id")
            prev = previews[primary]
            if isinstance(iid, int) and isinstance(prev, dict) and prev.get("ok"):
                await persist_profile_og_cache(
                    bridge,
                    identity_id=iid,
                    env=env,
                    profile_url=primary,
                    preview=prev,
                )
                merged = dict(facts)
                merged.update(facts_from_link_preview(primary, prev))
                c["preview_facts"] = merged

    _sync_legacy_link_preview(candidates)


def _sync_legacy_link_preview(candidates: list[dict[str, Any]]) -> None:
    """Keep ``link_preview`` aligned with primary URL for older FE paths."""
    for c in candidates:
        if c.get("link_preview"):
            continue
        primary = _primary_profile_url(c)
        if not primary:
            continue
        previews = c.get("link_previews")
        if isinstance(previews, dict) and primary in previews:
            c["link_preview"] = previews[primary]
            continue
        facts = c.get("preview_facts") if isinstance(c.get("preview_facts"), dict) else {}
        cached = link_preview_from_facts(facts, primary)
        if cached:
            c["link_preview"] = cached
