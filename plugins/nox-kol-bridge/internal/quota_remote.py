"""Remote quota cache + ledger reconciliation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from internal.paths import nox_cache_root

_QUOTA_CACHE_TTL_SEC = 300


def _quota_cache_path() -> Path:
    return nox_cache_root() / "quota_snapshot_cache.json"


def read_cached_quota() -> Optional[dict[str, Any]]:
    path = _quota_cache_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = raw.get("fetched_at_epoch")
    envelope = raw.get("envelope")
    if not isinstance(fetched_at, (int, float)) or not isinstance(envelope, dict):
        return None
    if time.time() - float(fetched_at) > _QUOTA_CACHE_TTL_SEC:
        return None
    return envelope


def store_cached_quota(envelope: dict[str, Any]) -> None:
    path = _quota_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"fetched_at_epoch": time.time(), "envelope": envelope},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def remote_used_credits(envelope: dict[str, Any]) -> Optional[int]:
    """Extract used credits from a Nox ``quota`` envelope."""
    if not envelope.get("success"):
        return None
    credits = envelope.get("credits")
    if isinstance(credits, dict) and credits.get("used") is not None:
        try:
            return int(credits["used"])
        except (TypeError, ValueError):
            pass
    data = envelope.get("data")
    if isinstance(data, dict) and data.get("used_credit") is not None:
        try:
            return int(data["used_credit"])
        except (TypeError, ValueError):
            pass
    return None
