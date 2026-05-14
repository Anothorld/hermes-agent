"""Veedcrawl integration plugin — bundled, auto-loaded.

Registers 5 tools (``account``, ``metadata``, ``transcript``, ``extract``,
``profile``) into the ``veedcrawl`` toolset. Each tool is gated by
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
    VEEDCRAWL_METADATA_SCHEMA,
    VEEDCRAWL_PROFILE_SCHEMA,
    VEEDCRAWL_TRANSCRIPT_SCHEMA,
    _check_veedcrawl_available,
    _handle_account,
    _handle_extract,
    _handle_metadata,
    _handle_profile,
    _handle_transcript,
)

_TOOLS = (
    ("veedcrawl_account",    VEEDCRAWL_ACCOUNT_SCHEMA,    _handle_account,    "🪪"),
    ("veedcrawl_metadata",   VEEDCRAWL_METADATA_SCHEMA,   _handle_metadata,   "📝"),
    ("veedcrawl_transcript", VEEDCRAWL_TRANSCRIPT_SCHEMA, _handle_transcript, "🎙️"),
    ("veedcrawl_extract",    VEEDCRAWL_EXTRACT_SCHEMA,    _handle_extract,    "🧪"),
    ("veedcrawl_profile",    VEEDCRAWL_PROFILE_SCHEMA,    _handle_profile,    "👤"),
)


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
