"""File-backed SERP result cache.

Caches normalized SERP responses on disk keyed by (provider, query, gl, hl) so
re-runs and parallel brainstorm queries don't burn API quota. Lives under the
Hermes data dir (``$HERMES_HOME/.cache/serp-api``) so it persists across
container restarts on the bind-mounted data volume.

Format: one JSON file per cache key, containing ``{"ts": epoch, "payload": ...}``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _cache_dir() -> Path:
    """Resolve the cache directory from env, falling back to HERMES_HOME."""
    env = os.environ.get("SERP_API_CACHE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    home = os.environ.get("HERMES_HOME", "").strip() or str(Path.home() / ".hermes")
    return Path(home) / ".cache" / "serp-api"


def _cache_key(provider: str, query: str, gl: str, hl: str) -> str:
    raw = f"{provider}|{query.lower().strip()}|{gl}|{hl}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def cache_get(provider: str, query: str, gl: str, hl: str, ttl_s: int) -> Any | None:
    """Return cached payload if fresh (within ``ttl_s``), else None."""
    if ttl_s <= 0:
        return None
    path = _cache_dir() / f"{_cache_key(provider, query, gl, hl)}.json"
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            entry = json.load(fh)
        if time.time() - float(entry.get("ts", 0)) > ttl_s:
            return None
        return entry.get("payload")
    except Exception:
        return None


def cache_put(provider: str, query: str, gl: str, hl: str, payload: Any) -> None:
    """Write ``payload`` to the cache (best-effort; never raises)."""
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_cache_key(provider, query, gl, hl)}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "payload": payload}, fh, ensure_ascii=False)
    except Exception:
        # Cache is an optimization; never let it break a SERP call.
        pass
