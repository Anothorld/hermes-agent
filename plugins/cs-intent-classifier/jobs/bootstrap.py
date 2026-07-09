"""Bootstrap plugin modules for cron jobs under ``jobs/``.

Jobs are not subpackages of ``cs_intent_classifier_pkg``; they run as scripts
from the plugin root. This loader mirrors ``serve.py`` / ``tests/conftest.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "cs_intent_classifier_pkg"

_SUBMODULES = (
    "schemas",
    "db",
    "classifier",
    "intent_provider",
    "plugin_api",
    "eval_runner",
    "learning",
    "keyword_learning",
)


def load_pkg() -> ModuleType:
    """Load (or return cached) synthetic package for the plugin."""
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    if str(_PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(_PLUGIN_ROOT))

    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in _SUBMODULES:
        path = _PLUGIN_ROOT / f"{sub}.py"
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            path,
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG_NAME
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


def __getattr__(name: str):
    if name in _SUBMODULES:
        return getattr(load_pkg(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
