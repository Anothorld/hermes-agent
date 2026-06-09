"""Thin import surface for ``kol_reply_dispatcher.run_once``.

Keeps matcher/dedup/gateway logic in the CLI script while letting bridge
modules call a single stable entry point.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Callable, Type

_RUN_ONCE: Callable[..., dict[str, int]] | None = None
_GMAIL_UNAVAILABLE: Type[Exception] | None = None


def import_run_once() -> tuple[Callable[..., dict[str, int]], Type[Exception]]:
    """Load ``run_once`` from ``scripts/kol_reply_dispatcher.py`` (cached)."""
    global _RUN_ONCE, _GMAIL_UNAVAILABLE
    if _RUN_ONCE is not None and _GMAIL_UNAVAILABLE is not None:
        return _RUN_ONCE, _GMAIL_UNAVAILABLE

    plugin_dir = Path(__file__).resolve().parent
    scripts_dir = plugin_dir / "scripts"
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        sys.modules[pkg_name] = pkg
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import kol_reply_dispatcher as disp  # noqa: WPS433 — script entry by design

    _RUN_ONCE = disp.run_once
    _GMAIL_UNAVAILABLE = disp.GmailUnavailable
    return _RUN_ONCE, _GMAIL_UNAVAILABLE
