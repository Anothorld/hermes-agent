"""Facebook Creator Discovery — Hermes plugin (bundled, auto-loaded).

Registers three tools (``fb_creator_search`` / ``fb_creator_profile`` /
``fb_content_search``) wrapping the Meta Graph API
``/creator_marketplace/creators`` and ``/creator_marketplace/content``
endpoints. The tools enable an agent to find Facebook creators that match
a brand's interest / audience / engagement criteria.

Authentication
--------------
Credentials are resolved by :class:`EnvFileCredentialProvider` from either
the ``FB_CREATOR_PAGE_TOKEN`` environment variable or the JSON config file
at ``~/.hermes/fb_creator.json``. Tokens are never logged or echoed back.

See ``README.md`` in this directory for setup instructions.
"""

from __future__ import annotations

from .tools import (
    FB_CONTENT_SEARCH_SCHEMA,
    FB_CREATOR_PROFILE_SCHEMA,
    FB_CREATOR_SEARCH_SCHEMA,
    _check_fb_creator_available,
    _handle_fb_content_search,
    _handle_fb_creator_profile,
    _handle_fb_creator_search,
)

__all__ = ["register"]


_TOOLS = (
    ("fb_creator_search",  FB_CREATOR_SEARCH_SCHEMA,  _handle_fb_creator_search,  "🔎"),
    ("fb_creator_profile", FB_CREATOR_PROFILE_SCHEMA, _handle_fb_creator_profile, "👤"),
    ("fb_content_search",  FB_CONTENT_SEARCH_SCHEMA,  _handle_fb_content_search,  "📰"),
)


def register(ctx) -> None:
    """Register all FB Creator Discovery tools. Called once by the loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="fb_creator_discovery",
            schema=schema,
            handler=handler,
            check_fn=_check_fb_creator_available,
            emoji=emoji,
        )
