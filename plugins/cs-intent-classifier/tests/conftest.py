"""Shared test setup — load the plugin as a synthetic package.

Mirrors serve.py's _load_pkg() so tests can import cs_intent_classifier_pkg.*
without needing the standalone runner.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "cs_intent_classifier_pkg"


def _load_pkg() -> ModuleType:
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in ("schemas", "db", "classifier", "intent_provider", "plugin_api"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG_NAME
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


_load_pkg()
