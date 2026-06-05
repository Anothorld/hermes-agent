"""Load ``kol-ops-bridge.veedcrawl_persist`` from the sibling plugin directory."""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


@lru_cache(maxsize=1)
def _load_persist_module() -> ModuleType:
    bridge_root = Path(__file__).resolve().parents[2] / "kol-ops-bridge"
    path = bridge_root / "veedcrawl_persist.py"
    if not path.is_file():
        raise ImportError(f"kol-ops-bridge veedcrawl_persist not found at {path}")
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_veedcrawl_persist",
        path,
        submodule_search_locations=[str(bridge_root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("failed to load veedcrawl_persist spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def fetch_with_persist(**kwargs: Any) -> dict[str, Any]:
    """Delegate to kol-ops-bridge ``fetch_with_persist``."""
    return _load_persist_module().fetch_with_persist(**kwargs)


def get_fetch_with_persist() -> Callable[..., dict[str, Any]]:
    return _load_persist_module().fetch_with_persist
