"""Profile-aware paths for nox cache and ledger."""

from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    """Return ``HERMES_HOME`` (profile-aware)."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def nox_cache_root() -> Path:
    """Directory for ``nox_cache.db`` and blobs."""
    root = hermes_home() / "kol-ops-bridge" / "nox_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def nox_cache_db_path() -> Path:
    return nox_cache_root() / "nox_cache.db"


def fixtures_root() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures"
