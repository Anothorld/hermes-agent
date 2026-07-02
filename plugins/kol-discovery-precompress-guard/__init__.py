"""Pre-compression cleanup hook for KOL discovery sessions.

When context compression is about to fire on a ``kol-campaign:LIVE:*``
session, this plugin walks the soon-to-be-discarded ``messages`` for
``browser_navigate`` calls to instagram.com profiles and persists a
snapshot of handles that were visited but NOT yet ingested via
``ingest-confirmed-candidate``. The snapshot lands at
``/tmp/precompress_pending_<session>.json`` so the next rediscover
round's ``# resume_directives`` STEP_0 can recover them instead of
losing the partial work to compression.

Side-effect only — never blocks compression.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_hooks():
    hooks_path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "hermes_plugins.kol_discovery_precompress_guard.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {hooks_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.kol_discovery_precompress_guard"
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    """Register the ``pre_compress`` lifecycle hook."""
    hooks = _load_hooks()
    ctx.register_hook("pre_compress", hooks.pre_compress)
