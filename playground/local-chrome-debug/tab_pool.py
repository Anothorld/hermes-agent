#!/usr/bin/env python3
"""Backward-compatible re-export of the tab pool implementation.

Canonical module: ``plugins/local-chrome-tab-pool/internal/tab_pool.py``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_canonical():
    module_name = "hermes_local_chrome_tab_pool_internal"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached

    path = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "local-chrome-tab-pool"
        / "internal"
        / "tab_pool.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tab pool from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_canonical()

acquire = _mod.acquire
ensure_chrome_running = _mod.ensure_chrome_running
is_enabled = _mod.is_enabled
list_orphan_tabs = _mod.list_orphan_tabs
normalize_task_id = _mod.normalize_task_id
probe_chrome = _mod.probe_chrome
release = _mod.release
release_all = _mod.release_all

__all__ = [
    "acquire",
    "ensure_chrome_running",
    "is_enabled",
    "list_orphan_tabs",
    "normalize_task_id",
    "probe_chrome",
    "release",
    "release_all",
]
