"""Tool schemas + handlers for kol-discovery-rpa.

Each handler accepts the registry's ``arguments`` dict and returns a JSON
string built by ``tools.registry.tool_result`` / ``tool_error``.

Handler signature: ``(arguments: dict | None = None, **kwargs) -> str``
The ``task_id`` is passed via ``kwargs`` by the registry dispatcher.

Phase 1 handlers (rpa_check_ip, rpa_precheck_handle, rpa_fetch_ig_profile)
are fully implemented. Phase 2/3 handlers return ``not_yet_implemented``
until their internal modules are built.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from tools.registry import tool_error, tool_result

# Hyphenated directory can't use package imports — add internal/ to sys.path
_PLUGIN_DIR = Path(__file__).resolve().parent
_INTERNAL_DIR = str(_PLUGIN_DIR / "internal")
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import RpaError  # noqa: E402

# --------------------------------------------------------------------- gating

def _check_rpa_available() -> bool:
    """Return True unless KOL_RPA_ENABLED=0 (master kill switch)."""
    return os.environ.get("KOL_RPA_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# --------------------------------------------------------------------- schemas

RPA_CHECK_IP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Preflight check: navigate to ipinfo.io/json and verify the exit IP "
        "country. MUST be the first RPA call before any instagram.com navigation. "
        "If country != expected, stop the run with mode_gate_blocked: non-US."
    ),
    "properties": {
        "expected_country": {
            "type": "string",
            "default": "US",
            "description": "ISO country code to verify (default US).",
        },
    },
    "required": [],
    "additionalProperties": False,
}

RPA_PRECHECK_HANDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Zero page-load precheck: verify a handle is not in exclusion_set / "
        "skip list / outreach cooldown BEFORE navigating to the profile. "
        "Agent passes the bootstrap exclusion sets. Returns qualification "
        "with hard_discard=True if the handle should be skipped."
    ),
    "properties": {
        "handle": {
            "type": "string",
            "description": "IG handle to check (with or without @).",
        },
        "exclusion_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Handles already in CAL (from list-candidates).",
        },
        "skip_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Handles in discovery skip set (competitor/success/aborted/legacy_collab).",
        },
        "cooldown_handles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Handles in 14-day outreach cooldown.",
        },
        "candidate_status_map": {
            "type": "object",
            "description": "Optional handle→status map from list-candidate-handles --with-status.",
        },
    },
    "required": ["handle"],
    "additionalProperties": False,
}

RPA_FETCH_IG_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Navigate to instagram.com/<handle>/ and extract structured profile "
        "data (followers, bio, account_location, bio_links, is_business, etc.) "
        "plus profile-level qualification gates (followers ≥80k, region US/CA, "
        "account type, furniture self-commerce heuristic). The account country "
        "is read from the '...' → '账户简介' / 'About this account' dialog "
        "(authoritative, locale-rendered) and used as the primary region signal. "
        "Returns qualification.hard_discard=True if any mechanical gate fails — "
        "Agent MUST discard, cannot override with learned criteria."
    ),
    "properties": {
        "handle": {
            "type": "string",
            "description": "IG handle (with or without @).",
        },
        "include_bio_links": {
            "type": "boolean",
            "default": True,
            "description": "Whether to extract link-in-bio URLs.",
        },
        "include_account_location": {
            "type": "boolean",
            "default": True,
            "description": (
                "Whether to click the header '...' → '账户简介' / 'About this "
                "account' dialog to read the authoritative account country "
                "('账户所在地'). Adds ~2-3s + 2 clicks per profile. Default True "
                "— the dialog country is far more reliable for the region gate "
                "than guessing from bio text. Set False for faster batch runs "
                "where region can be inferred from bio."
            ),
        },
    },
    "required": ["handle"],
    "additionalProperties": False,
}

RPA_FETCH_IG_REELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Navigate to the profile Reels tab and extract up to max_reels items "
        "(default 10 — most recent on the grid) with url, views, and "
        "thumbnail_url scraped from the grid img[src]. Also returns a "
        "content_eval plan: cover_reels = first 2 for vision screening; "
        "video_reels = random 3 from the recent-10 pool when video eval is ON. Pass "
        "data.content_eval to rpa_download_ig_content for batch download. "
        "Runs reels-level qualification gates (≥5 reels/3mo, avg views ≥20k, "
        "reel ER ≥2%, static-only discard)."
    ),
    "properties": {
        "handle": {"type": "string", "description": "IG handle."},
        "max_reels": {
            "type": "integer",
            "default": 10,
            "maximum": 20,
            "description": "Max reels to extract (default 10 for content screening).",
        },
        "include_content_eval": {
            "type": "boolean",
            "default": True,
            "description": (
                "Attach content_eval block (2 cover reels for vision + random "
                "3 video sample from recent-10 when video mode ON). Default True."
            ),
        },
    },
    "required": ["handle"],
    "additionalProperties": False,
}

RPA_FETCH_GOOGLE_SERP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Navigate to google.com/search?q=<query> and extract SERP results "
        "(title, url, snippet, rank) plus candidate_handles from profile URLs, "
        "@mentions, and (by default) authors resolved from /reel/ and /p/ URLs. "
        "Expect dozens of candidate_handles per query when resolve_authors=true. "
        "Use candidate_handles for bulk rpa_precheck_handle + "
        "rpa_fetch_ig_profile (followers/region) triage."
    ),
    "properties": {
        "query": {"type": "string", "description": "Search query (will be URL-encoded)."},
        "max_results": {
            "type": "integer",
            "default": 30,
            "maximum": 40,
            "description": "Organic result rows to return (default 30, max 40).",
        },
        "resolve_authors": {
            "type": "boolean",
            "default": True,
            "description": (
                "Open reel/post URLs from the SERP to recover author handles "
                "(default true). Disable only for cheap SERP-only probes."
            ),
        },
        "max_author_resolves": {
            "type": "integer",
            "default": 20,
            "maximum": 30,
            "description": (
                "Max reel/post pages to open per SERP call when resolving "
                "authors (default 20, max 30)."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

RPA_FETCH_HASHTAG_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Navigate to instagram.com/explore/tags/<tag>/ and extract candidate handles.",
    "properties": {
        "tag": {"type": "string", "description": "Hashtag without #."},
        "max_candidates": {"type": "integer", "default": 30, "maximum": 30},
    },
    "required": ["tag"],
    "additionalProperties": False,
}

RPA_DOWNLOAD_IG_REEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Download an IG Reel as MP4 via yt-dlp + local-chrome cookies. Also "
        "downloads the cover image (--write-thumbnail) for vision_analyze. "
        "BLOCKED when KOL_RPA_VIDEO_EVAL_ENABLED=0 or brief rpa_video_eval_enabled=false "
        "(use rpa_download_ig_cover for cover-only in cover mode). "
        "Max 3 downloads per candidate when enabled. Auto-cleans files >1h old. "
        "Returns file_path, file_size_bytes, thumbnail_url, cover_path, reel_id. "
        "Requires yt-dlp on PATH (pip install yt-dlp); ffmpeg recommended for best quality."
    ),
    "properties": {
        "reel_url": {"type": "string", "description": "IG Reel URL."},
        "quality": {"type": "string", "default": "best"},
    },
    "required": ["reel_url"],
    "additionalProperties": False,
}

RPA_DOWNLOAD_IG_COVER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Download a single IG Reel cover image (no video). Prefer "
        "thumbnail_url from rpa_fetch_ig_reels grid RPA (HTTP fetch); "
        "falls back to yt-dlp --write-thumbnail --skip-download. For cover-mode "
        "content screening (KOL_RPA_VIDEO_EVAL_ENABLED=0). Not gated by the "
        "video-eval switch. Returns cover_path, file_size_bytes, thumbnail_url, "
        "reel_id, source."
    ),
    "properties": {
        "reel_url": {"type": "string", "description": "IG Reel URL."},
        "thumbnail_url": {
            "type": "string",
            "description": (
                "Optional grid thumbnail URL from rpa_fetch_ig_reels — "
                "downloaded directly without yt-dlp when provided."
            ),
        },
    },
    "required": ["reel_url"],
    "additionalProperties": False,
}

RPA_DOWNLOAD_IG_CONTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Batch-download content-eval assets after rpa_fetch_ig_reels. "
        "Downloads cover_reels (default 2 profile-grid thumbnails → local files "
        "for vision_analyze). When video eval is ON, also downloads video_reels "
        "(random 3 from the recent-10 pool) as MP4 for video_analyze. Pass "
        "the data.content_eval block from the fetch response. Partial failures "
        "are reported per reel in data.errors."
    ),
    "properties": {
        "content_eval": {
            "type": "object",
            "description": (
                "The content_eval object from rpa_fetch_ig_reels (cover_reels, "
                "video_reels, eval_mode, targets)."
            ),
        },
    },
    "required": ["content_eval"],
    "additionalProperties": False,
}

RPA_FETCH_REEL_COMMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Navigate to a Reel page and extract comments. mode=evaluation returns "
        "comments+caption+hashtags+thumbnail_url+reel_likes+reel_comments_count "
        "(for KOL content screening + real ER computation). "
        "mode=discovery returns commenter handles with follower hints (for "
        "lateral discovery). First viewport only — does not scroll or expand replies."
    ),
    "properties": {
        "reel_url": {"type": "string"},
        "mode": {"type": "string", "enum": ["evaluation", "discovery"], "default": "evaluation"},
        "max_items": {"type": "integer", "default": 15},
        "min_followers_hint": {"type": "integer", "default": 80000},
        "include_caption": {"type": "boolean", "default": True},
        "scroll_comments": {"type": "integer", "default": 0},
    },
    "required": ["reel_url"],
    "additionalProperties": False,
}

RPA_CLEANUP_REELS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Delete downloaded Reel MP4 files older than specified hours.",
    "properties": {
        "older_than_hours": {"type": "number", "default": 1},
    },
    "required": [],
    "additionalProperties": False,
}

RPA_FETCH_SIMILAR_ACCOUNTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Extract similar/suggested accounts from an IG profile page.",
    "properties": {
        "handle": {"type": "string"},
        "max_accounts": {"type": "integer", "default": 15, "maximum": 30},
    },
    "required": ["handle"],
    "additionalProperties": False,
}

RPA_FETCH_FOLLOWING_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Extract the following list from instagram.com/<handle>/following/.",
    "properties": {
        "handle": {"type": "string"},
        "max_accounts": {"type": "integer", "default": 30, "maximum": 50},
        "scroll_attempts": {"type": "integer", "default": 2, "maximum": 3},
    },
    "required": ["handle"],
    "additionalProperties": False,
}

# --------------------------------------------------------------------- descriptions

_TOOL_DESCRIPTIONS = {
    "rpa_check_ip": "Preflight: verify US exit IP via ipinfo.io. Must be first RPA call.",
    "rpa_precheck_handle": "Zero page-load precheck: is handle in exclusion/skip/cooldown set?",
    "rpa_fetch_ig_profile": "Extract IG profile data + profile-level qualification gates (followers, region, account type).",
    "rpa_fetch_ig_reels": "Extract IG Reels list + reels-level qualification gates (count, views, ER).",
    "rpa_fetch_google_serp": "Google SERP extraction for public-web KOL discovery.",
    "rpa_fetch_hashtag_candidates": "Extract candidate handles from IG hashtag explore page.",
    "rpa_download_ig_reel": "Download IG Reel MP4 via yt-dlp (video eval mode only).",
    "rpa_download_ig_cover": "Download one IG Reel cover (RPA thumbnail URL or yt-dlp fallback).",
    "rpa_download_ig_content": "Batch download content_eval covers + random video sample.",
    "rpa_fetch_reel_comments": "Extract Reel comments (evaluation: style/audience; discovery: commenter handles).",
    "rpa_cleanup_reels": "Delete downloaded Reel MP4 files older than threshold.",
    "rpa_fetch_similar_accounts": "Extract similar/suggested accounts from IG profile.",
    "rpa_fetch_following_list": "Extract following list from IG profile.",
}


def as_function_schema(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Wrap a JSON-Schema parameters object in an OpenAI function schema."""
    params = dict(parameters)
    description = str(
        params.pop("description", "") or _TOOL_DESCRIPTIONS.get(tool_name, tool_name)
    )
    return {
        "name": tool_name,
        "description": description,
        "parameters": params,
    }


# --------------------------------------------------------------------- error wrapper

def _grant_fallback_for_error(handler_name: str, args: dict, task_id: str) -> None:
    """Grant a one-shot browser fallback token when an RPA tool fails with DomChangedError.

    Maps the RPA tool's args to the URL that browser_navigate would use,
    so the guard allows ONE browser fallback to the same URL.
    """
    try:
        import importlib.util
        from pathlib import Path
        ds_path = Path(__file__).resolve().parents[1] / "kol-bridge-agent-guard" / "internal" / "discovery_session.py"
        if not ds_path.exists():
            return
        spec = importlib.util.spec_from_file_location("kol_bridge_agent_guard_discovery_session_fb", ds_path)
        if spec is None or spec.loader is None:
            return
        ds_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ds_mod)

        # Map handler → URL that browser_navigate would target
        handle = args.get("handle", "").lstrip("@")
        reel_url = args.get("reel_url", "")
        query = args.get("query", "")

        url = None
        if "ig_profile" in handler_name and handle:
            url = f"https://www.instagram.com/{handle}/"
        elif "ig_reels" in handler_name and handle:
            url = f"https://www.instagram.com/{handle}/reels/"
        elif "google_serp" in handler_name and query:
            from urllib.parse import quote_plus
            url = f"https://www.google.com/search?q={quote_plus(query)}"
        elif "reel_comments" in handler_name and reel_url:
            url = reel_url

        if url:
            ds_mod.grant_rpa_fallback(task_id, url)
    except Exception:
        pass  # best-effort; guard may not be loaded


def _wrap_errors(handler: Callable) -> Callable:
    """Decorator: catch RpaError subclasses and return structured tool_error.

    On DomChangedError, also grants a one-shot browser fallback token so the
    Agent can use browser_navigate to the same URL as a fallback.
    """

    def wrapper(
        arguments: dict[str, Any] | None = None,
        *pos_args: Any,
        **kwargs: Any,
    ) -> str:
        args = dict(arguments or {})
        # The Hermes registry passes ``task_id`` inconsistently across call
        # sites (2nd positional arg, keyword, or nested inside ``arguments``).
        # Normalize to a single keyword so handlers never hit
        # "got multiple values for keyword argument 'task_id'". Always pull
        # task_id out of args so handlers don't see a stale copy.
        task_id = kwargs.pop("task_id", "")
        if not task_id and pos_args and isinstance(pos_args[0], str):
            task_id = pos_args[0]
        nested_tid = args.pop("task_id", "")
        if not task_id and isinstance(nested_tid, str):
            task_id = nested_tid
        kwargs["task_id"] = task_id
        try:
            return handler(args, **kwargs)
        except RpaError as exc:
            # Grant fallback token for DOM failures so Agent can use browser_*
            if exc.code == "dom_changed":
                _grant_fallback_for_error(handler.__name__, args, task_id)
            return tool_error(
                str(exc),
                code=exc.code,
                detail=exc.detail,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            return tool_error(
                f"Unexpected error in {handler.__name__}: {exc}",
                code="internal_error",
                traceback=tb[:2000],
            )

    wrapper.__name__ = handler.__name__
    return wrapper


# --------------------------------------------------------------------- meta helper

def _meta(task_id: str, elapsed_ms: int = 0, page_loads: int = 1,
           pacing_delay_ms: int = 0, risk: str | None = None, **extra: Any) -> dict:
    """Build the meta block for tool responses.

    Includes quota snapshot (nested under ``run_quota``) and risk detection
    result, matching the plan's meta schema.
    """
    import pacing
    quota = pacing.quota_snapshot(task_id)
    meta: dict[str, Any] = {
        "elapsed_ms": elapsed_ms,
        "page_loads": page_loads,
        "pacing_delay_ms": pacing_delay_ms,
        "run_quota": quota,
        "risk": {"detected": risk},
        "fallback_hint": "use browser_navigate + browser_snapshot if errors persist",
    }
    meta.update(extra)
    return meta


# --------------------------------------------------------------------- Phase 1 handlers

def _handle_check_ip(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_check_ip."""
    from cdp_runner import CdpRunner
    from ip_check import check_ip

    runner = CdpRunner(task_id)
    expected = args.get("expected_country", "US")
    data = check_ip(runner, expected_country=expected)

    if not data["ok"]:
        return tool_result({
            "ok": False,
            "data": data,
            "errors": [{"code": "non_us_ip", "detail": f"exit country={data.get('country')}, expected={expected}"}],
            "meta": _meta(task_id, page_loads=1),
        })

    return tool_result({
        "ok": True,
        "data": data,
        "errors": [],
        "meta": _meta(task_id, page_loads=1),
    })


def _handle_precheck_handle(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_precheck_handle — zero page load."""
    from precheck import precheck_handle

    handle = args.get("handle", "")
    if not handle:
        return tool_error("handle is required", code="missing_arg")

    result = precheck_handle(
        handle,
        exclusion_handles=args.get("exclusion_handles"),
        skip_handles=args.get("skip_handles"),
        cooldown_handles=args.get("cooldown_handles"),
        candidate_status_map=args.get("candidate_status_map"),
    )

    return tool_result({
        "ok": True,
        "data": {
            "handle": handle.lstrip("@"),
            "exclusion_precheck": result["gates"]["exclusion_precheck"],
        },
        "qualification": result,
        "errors": [],
        "meta": _meta(task_id, page_loads=0),
    })


def _handle_fetch_ig_profile(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_fetch_ig_profile."""
    from cdp_runner import CdpRunner
    from ig_profile import fetch_profile

    handle = args.get("handle", "")
    if not handle:
        return tool_error("handle is required", code="missing_arg")

    runner = CdpRunner(task_id)
    result = fetch_profile(
        runner,
        handle,
        include_bio_links=args.get("include_bio_links", True),
        include_account_location=args.get("include_account_location", True),
    )

    return tool_result({
        "ok": True,
        "data": result["data"],
        "qualification": result["qualification"],
        "errors": [],
        "meta": _meta(task_id, page_loads=1),
    })


# --------------------------------------------------------------------- Phase 2/3 stubs

def _not_yet_implemented(tool_name: str) -> str:
    return tool_error(
        f"{tool_name} is not yet implemented in this phase",
        code="not_yet_implemented",
    )


def _handle_fetch_ig_reels(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_fetch_ig_reels."""
    from cdp_runner import CdpRunner
    from ig_reels import fetch_reels

    handle = args.get("handle", "")
    if not handle:
        return tool_error("handle is required", code="missing_arg")

    max_reels = min(int(args.get("max_reels", 10)), 20)
    include_content_eval = args.get("include_content_eval", True)
    runner = CdpRunner(task_id)
    result = fetch_reels(
        runner,
        handle,
        max_reels=max_reels,
        include_content_eval=bool(include_content_eval),
    )

    return tool_result({
        "ok": True,
        "data": result["data"],
        "qualification": result["qualification"],
        "errors": [],
        "meta": _meta(task_id, page_loads=1),
    })


def _handle_fetch_google_serp(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_fetch_google_serp."""
    from cdp_runner import CdpRunner
    from google_serp import fetch_serp

    query = args.get("query", "")
    if not query:
        return tool_error("query is required", code="missing_arg")

    max_results = min(int(args.get("max_results", 30)), 40)
    resolve_authors = args.get("resolve_authors", True)
    if isinstance(resolve_authors, str):
        resolve_authors = resolve_authors.strip().lower() not in {"0", "false", "no"}
    max_author_resolves = min(int(args.get("max_author_resolves", 20)), 30)
    runner = CdpRunner(task_id)
    result = fetch_serp(
        runner,
        query,
        max_results=max_results,
        resolve_authors=bool(resolve_authors),
        max_author_resolves=max_author_resolves,
    )
    data = result["data"]
    page_loads = 1 + int(data.get("author_navigations") or 0)

    return tool_result({
        "ok": True,
        "data": data,
        "errors": [],
        "meta": _meta(task_id, page_loads=page_loads),
    })


def _handle_download_ig_reel(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_download_ig_reel."""
    from reel_download import download_reel

    reel_url = args.get("reel_url", "")
    if not reel_url:
        return tool_error("reel_url is required", code="missing_arg")

    quality = args.get("quality", "best")
    result = download_reel(reel_url, quality=quality)

    return tool_result({
        "ok": True,
        "data": result,
        "errors": [],
        "meta": _meta(task_id, page_loads=0),
    })


def _handle_download_ig_cover(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_download_ig_cover — cover-image-only download for cover mode."""
    from reel_download import download_cover

    reel_url = args.get("reel_url", "")
    if not reel_url:
        return tool_error("reel_url is required", code="missing_arg")

    thumbnail_url = args.get("thumbnail_url") or None
    result = download_cover(reel_url, thumbnail_url=thumbnail_url)

    return tool_result({
        "ok": True,
        "data": result,
        "errors": [],
        "meta": _meta(task_id, page_loads=0),
    })


def _handle_download_ig_content(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_download_ig_content — batch cover + video download."""
    from reel_download import download_content_eval

    content_eval = args.get("content_eval")
    if not isinstance(content_eval, dict) or not content_eval.get("cover_reels"):
        return tool_error(
            "content_eval with cover_reels is required (from rpa_fetch_ig_reels)",
            code="missing_arg",
        )

    result = download_content_eval(content_eval)
    # At least one cover must land; video mode also requires ≥1 video when
    # videos were requested. Never report ok when every cover failed.
    covers_ok = int(result.get("covers_downloaded") or 0) > 0
    videos_target = int(result.get("videos_target") or 0)
    videos_ok = int(result.get("videos_downloaded") or 0) > 0
    ok = covers_ok and (videos_ok if videos_target > 0 else True)

    return tool_result({
        "ok": ok,
        "data": result,
        "errors": result.get("errors") or [],
        "meta": _meta(task_id, page_loads=0),
    })


def _handle_fetch_reel_comments(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_fetch_reel_comments."""
    from cdp_runner import CdpRunner
    from reel_comments import fetch_reel_comments

    reel_url = args.get("reel_url", "")
    if not reel_url:
        return tool_error("reel_url is required", code="missing_arg")

    mode = args.get("mode", "evaluation")
    max_items = int(args.get("max_items", 15))
    include_caption = args.get("include_caption", True)
    min_followers_hint = int(args.get("min_followers_hint", 80000))
    scroll_comments = int(args.get("scroll_comments", 0))

    runner = CdpRunner(task_id)
    result = fetch_reel_comments(
        runner, reel_url, mode=mode, max_items=max_items,
        include_caption=include_caption,
        min_followers_hint=min_followers_hint,
        scroll_comments=scroll_comments,
    )

    return tool_result({
        "ok": True,
        "data": result["data"],
        "errors": [],
        "meta": _meta(task_id, page_loads=1),
    })


def _handle_cleanup_reels(args: dict, *, task_id: str = "", **_: Any) -> str:
    """Handle rpa_cleanup_reels."""
    from reel_download import cleanup_reels

    older_than = float(args.get("older_than_hours", 1))
    result = cleanup_reels(older_than_hours=older_than)

    return tool_result({
        "ok": True,
        "data": result,
        "errors": [],
        "meta": _meta(task_id, page_loads=0),
    })


def _handle_fetch_hashtag_candidates(args: dict, *, task_id: str = "", **_: Any) -> str:
    return _not_yet_implemented("rpa_fetch_hashtag_candidates")


def _handle_fetch_similar_accounts(args: dict, *, task_id: str = "", **_: Any) -> str:
    return _not_yet_implemented("rpa_fetch_similar_accounts")


def _handle_fetch_following_list(args: dict, *, task_id: str = "", **_: Any) -> str:
    return _not_yet_implemented("rpa_fetch_following_list")


# --------------------------------------------------------------------- exports

__all__ = (
    "RPA_CHECK_IP_SCHEMA",
    "RPA_PRECHECK_HANDLE_SCHEMA",
    "RPA_FETCH_IG_PROFILE_SCHEMA",
    "RPA_FETCH_IG_REELS_SCHEMA",
    "RPA_FETCH_GOOGLE_SERP_SCHEMA",
    "RPA_FETCH_HASHTAG_CANDIDATES_SCHEMA",
    "RPA_DOWNLOAD_IG_REEL_SCHEMA",
    "RPA_DOWNLOAD_IG_COVER_SCHEMA",
    "RPA_DOWNLOAD_IG_CONTENT_SCHEMA",
    "RPA_FETCH_REEL_COMMENTS_SCHEMA",
    "RPA_CLEANUP_REELS_SCHEMA",
    "RPA_FETCH_SIMILAR_ACCOUNTS_SCHEMA",
    "RPA_FETCH_FOLLOWING_LIST_SCHEMA",
    "as_function_schema",
    "_check_rpa_available",
    "_handle_check_ip",
    "_handle_precheck_handle",
    "_handle_fetch_ig_profile",
    "_handle_fetch_ig_reels",
    "_handle_fetch_google_serp",
    "_handle_fetch_hashtag_candidates",
    "_handle_download_ig_reel",
    "_handle_download_ig_cover",
    "_handle_download_ig_content",
    "_handle_fetch_reel_comments",
    "_handle_cleanup_reels",
    "_handle_fetch_similar_accounts",
    "_handle_fetch_following_list",
)
