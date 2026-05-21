"""Pytest fixtures for kol-ops-bridge CAL tests.

Loads ``cal.py`` and ``schema.py`` as members of a synthetic package
``kol_ops_bridge_pkg`` so the existing relative imports keep working
despite the hyphenated plugin directory not being a valid Python
identifier.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_pkg"


def _load_package() -> types.ModuleType:
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG_NAME] = pkg

    for sub in ("schema", "goals", "cal"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


@pytest.fixture()
def cal_db(tmp_path):
    """Point CAL at a fresh temp DB for the duration of one test."""
    pkg = _load_package()
    cal_mod = pkg.cal  # type: ignore[attr-defined]
    db_file = tmp_path / "cal.db"
    cal_mod.set_db_path(db_file)
    yield cal_mod
    cal_mod.set_db_path(None)
