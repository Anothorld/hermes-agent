"""serp-api plugin — SERP API client (Google CSE / Serper / SerpAPI / Valueserp).

Registers the ``serp_fetch_google`` tool, a drop-in replacement for the
browser-based ``rpa_fetch_google_serp`` used by the SEO Studio brainstorm step.
Returns the same ``data.results[]`` shape (``rank``/``title``/``url``/``snippet``)
so the skill/playbook can consume it unchanged.

Note: the directory name ``serp-api`` has hyphens, so Python cannot import it as
a regular package. We use ``importlib.util`` to load ``tools.py``, matching the
pattern in ``kol-discovery-rpa``.

Provider selection is via the ``SERP_API_PROVIDER`` env var (default
``google_cse`` — the free tier). API keys are read from env; see README.md.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_tools():
    """Load tools.py via importlib (hyphenated dir can't use package imports)."""
    path = Path(__file__).resolve().with_name("tools.py")
    spec = importlib.util.spec_from_file_location("serp_api_tools", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tools from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    """Register the serp_fetch_google tool."""
    tools_mod = _load_tools()
    ctx.register_tool(
        name="serp_fetch_google",
        toolset="serp_api",
        schema=tools_mod.SERP_FETCH_GOOGLE_SCHEMA,
        handler=tools_mod._handle_fetch_google,
        check_fn=tools_mod._check_serp_api_available,
        emoji="🔍",
    )
