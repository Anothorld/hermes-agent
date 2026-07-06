"""cs-intent-classifier plugin registration entry.

This module is loaded by the Hermes plugin manager when the plugin is enabled.
It registers the plugin's HTTP router (plugin_api.router) and exposes metadata.

The plugin is an independent, switchable service:
- Own SQLite DB (cs_intent.db) — does not write cs-ops-bridge CAL.
- Own HTTP API on port 8082 (CS_INTENT_PORT).
- Own LLM config (CS_INTENT_LLM_* env vars) — does not read profile config.
- Enabled in cs-ops-bridge only when CS_INTENT_ENABLED=true; otherwise the
  two seams in cs-ops-bridge short-circuit and behavior is unchanged.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8082
DEFAULT_DB_NAME = "cs_intent.db"


def register(ctx) -> None:  # pragma: no cover — wired by plugin manager
    """Register the plugin with the Hermes plugin context.

    The router is exposed via ``plugin_api.router``; the plugin manager mounts
    it under ``/api/plugins/cs-intent-classifier`` when loaded in-process, or
    the standalone ``serve.py`` runner mounts it directly.
    """
    from . import plugin_api

    if hasattr(ctx, "register_router"):
        ctx.register_router(plugin_api.router)
