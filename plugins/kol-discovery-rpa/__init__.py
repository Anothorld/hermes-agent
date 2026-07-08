"""KOL Discovery RPA plugin — structured IG extraction via local-chrome CDP.

Replaces step-by-step ``browser_*`` calls in ``instagram-kol-discovery`` with
one-shot structured tools (``rpa_fetch_ig_profile``, ``rpa_fetch_ig_reels``,
``rpa_fetch_reel_comments``, etc.). Each tool reuses ``local-chrome-tab-pool``
for a page-level CDP connection, extracts data via ``cdp_page.evaluate``, and
returns a JSON envelope with a ``qualification`` block synced from the skill's
hard thresholds (``qualification_rules.py`` — single source of truth).

Phase 1: rpa_check_ip, rpa_precheck_handle, rpa_fetch_ig_profile
Phase 2: rpa_fetch_ig_reels, rpa_fetch_google_serp, rpa_download_ig_reel,
          rpa_download_ig_cover, rpa_fetch_reel_comments(evaluation), rpa_cleanup_reels
Phase 3: rpa_fetch_hashtag_candidates, rpa_fetch_reel_comments(discovery),
          rpa_fetch_similar_accounts, rpa_fetch_following_list

Note: the directory name ``kol-discovery-rpa`` has hyphens, so Python cannot
import it as a regular package. We use ``importlib.util`` to load ``tools.py``
and ``hooks.py``, matching the pattern in ``kol-bridge-agent-guard``.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_PHASE_1_COUNT = 3
_PHASE_2_COUNT = 9
_PHASE_3_COUNT = 12


def _active_tool_count() -> int:
    """Return how many tools to register based on ``KOL_RPA_PHASE`` env."""
    phase = int(os.environ.get("KOL_RPA_PHASE", "1"))
    if phase >= 3:
        return _PHASE_3_COUNT
    if phase >= 2:
        return _PHASE_2_COUNT
    return _PHASE_1_COUNT


def _load_tools():
    """Load tools.py via importlib (hyphenated dir can't use package imports)."""
    cached = _globals_dict().get("_tools_module")
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("tools.py")
    spec = importlib.util.spec_from_file_location(
        "kol_discovery_rpa_tools", path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tools from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _globals_dict()["_tools_module"] = module
    return module


def _globals_dict():
    return globals()


def _load_hooks():
    path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "kol_discovery_rpa_hooks"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Tool definitions: (name, schema_attr, handler_attr, emoji)
# Phase 1 = first 3; Phase 2 adds next 5; Phase 3 adds final 3.
_TOOL_NAMES = (
    # Phase 1
    ("rpa_check_ip",             "RPA_CHECK_IP_SCHEMA",             "_handle_check_ip",             "🌐"),
    # Phase 2
    ("rpa_precheck_handle",      "RPA_PRECHECK_HANDLE_SCHEMA",      "_handle_precheck_handle",      "🚪"),
    ("rpa_fetch_ig_profile",     "RPA_FETCH_IG_PROFILE_SCHEMA",     "_handle_fetch_ig_profile",     "👤"),
    ("rpa_fetch_ig_reels",       "RPA_FETCH_IG_REELS_SCHEMA",       "_handle_fetch_ig_reels",       "🎬"),
    ("rpa_fetch_google_serp",    "RPA_FETCH_GOOGLE_SERP_SCHEMA",    "_handle_fetch_google_serp",    "🔍"),
    ("rpa_download_ig_reel",     "RPA_DOWNLOAD_IG_REEL_SCHEMA",     "_handle_download_ig_reel",     "⬇️"),
    ("rpa_download_ig_cover",    "RPA_DOWNLOAD_IG_COVER_SCHEMA",    "_handle_download_ig_cover",    "🖼️"),
    ("rpa_fetch_reel_comments",  "RPA_FETCH_REEL_COMMENTS_SCHEMA",  "_handle_fetch_reel_comments",  "💬"),
    ("rpa_cleanup_reels",        "RPA_CLEANUP_REELS_SCHEMA",        "_handle_cleanup_reels",        "🧹"),
    # Phase 3
    ("rpa_fetch_hashtag_candidates", "RPA_FETCH_HASHTAG_CANDIDATES_SCHEMA", "_handle_fetch_hashtag_candidates", "#️⃣"),
    ("rpa_fetch_similar_accounts",   "RPA_FETCH_SIMILAR_ACCOUNTS_SCHEMA",   "_handle_fetch_similar_accounts",   "🔗"),
    ("rpa_fetch_following_list",     "RPA_FETCH_FOLLOWING_LIST_SCHEMA",     "_handle_fetch_following_list",     "👥"),
)


def register(ctx) -> None:
    """Register KOL Discovery RPA tools. Called once by the plugin loader."""
    tools_mod = _load_tools()
    count = _active_tool_count()

    for name, schema_attr, handler_attr, emoji in _TOOL_NAMES[:count]:
        schema = getattr(tools_mod, schema_attr)
        raw_handler = getattr(tools_mod, handler_attr)
        handler = tools_mod._wrap_errors(raw_handler)
        ctx.register_tool(
            name=name,
            toolset="kol-discovery-rpa",
            schema=tools_mod.as_function_schema(name, schema),
            handler=handler,
            check_fn=tools_mod._check_rpa_available,
            emoji=emoji,
        )

    hooks = _load_hooks()
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
