"""Veedcrawl integration plugin — bundled, auto-loaded.

Registers Veedcrawl tools into the ``veedcrawl`` toolset. Discovery tools
(``metadata``, ``extract``, ``instagram_profile``, ``search_social_videos``)
embed kol-ops-bridge monthly persist via ``fetch_with_persist``. Each tool is gated by
``_check_veedcrawl_available()`` — when ``VEEDCRAWL_API_KEY`` (or
``X_API_KEY``) is unset, the tools remain registered but the runtime check
prevents dispatch.

Why a plugin instead of a top-level ``tools/`` file? See
``plugins/spotify/__init__.py`` — same rationale.
"""

from __future__ import annotations

from plugins.veedcrawl.tools import (
    VEEDCRAWL_ACCOUNT_SCHEMA,
    VEEDCRAWL_EXTRACT_SCHEMA,
    VEEDCRAWL_JOB_SCHEMA,
    VEEDCRAWL_METADATA_SCHEMA,
    VEEDCRAWL_PROFILE_SCHEMA,
    VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA,
    VEEDCRAWL_SEARCH_SCHEMA,
    VEEDCRAWL_TRANSCRIPT_SCHEMA,
    _check_veedcrawl_available,
    _handle_account,
    _handle_extract,
    _handle_job,
    _handle_metadata,
    _handle_profile,
    _handle_instagram_profile,
    _handle_search,
    _handle_transcript,
)

_TOOLS = (
    ("veedcrawl_account",             VEEDCRAWL_ACCOUNT_SCHEMA,             _handle_account,             "🪪"),
    ("veedcrawl_metadata",            VEEDCRAWL_METADATA_SCHEMA,            _handle_metadata,            "📝"),
    ("veedcrawl_transcript",          VEEDCRAWL_TRANSCRIPT_SCHEMA,          _handle_transcript,          "🎙️"),
    ("veedcrawl_extract",             VEEDCRAWL_EXTRACT_SCHEMA,             _handle_extract,             "🧪"),
    ("veedcrawl_profile",             VEEDCRAWL_PROFILE_SCHEMA,             _handle_profile,             "👤"),
    ("veedcrawl_instagram_profile",   VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA,   _handle_instagram_profile,   "📸"),
    ("veedcrawl_search_social_videos", VEEDCRAWL_SEARCH_SCHEMA,             _handle_search,              "🔍"),
    ("veedcrawl_job",                 VEEDCRAWL_JOB_SCHEMA,                 _handle_job,                 "🔁"),
)


def _load_hooks():
    import importlib.util
    from pathlib import Path

    hooks_path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "hermes_plugins.veedcrawl.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {hooks_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.veedcrawl"
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    """Register all Veedcrawl tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="veedcrawl",
            schema=schema,
            handler=handler,
            check_fn=_check_veedcrawl_available,
            emoji=emoji,
        )
    hooks = _load_hooks()
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
