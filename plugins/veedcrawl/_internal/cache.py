"""On-disk JSON cache for Veedcrawl responses.

Cache key = SHA-256 of ``f"{endpoint}|{canonical_json(params)}"``. Files live
under ``~/.hermes/cache/veedcrawl/<endpoint>/<key>.json``. Each entry stores
``{stored_at, ttl_s, payload}``; ``ttl_s = None`` means permanent (idempotent
async-job results never need to be refetched).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# Sentinel TTL value meaning "never expires".
PERMANENT: None = None


def _hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def cache_root() -> Path:
    """Return the cache root, creating it lazily."""
    root = _hermes_home() / "cache" / "veedcrawl"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _canonical(params: Any) -> str:
    """Stable JSON for hashing — sorted keys, no whitespace."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def make_key(endpoint: str, params: Any) -> str:
    raw = f"{endpoint}|{_canonical(params)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path_for(endpoint: str, key: str) -> Path:
    safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
    bucket = cache_root() / safe_endpoint
    bucket.mkdir(parents=True, exist_ok=True)
    return bucket / f"{key}.json"


def get(endpoint: str, params: Any) -> Optional[dict[str, Any]]:
    """Return the cached payload if present and unexpired, else ``None``."""
    key = make_key(endpoint, params)
    path = _path_for(endpoint, key)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ttl = entry.get("ttl_s")
    stored_at = entry.get("stored_at", 0)
    if ttl is not None:
        if (time.time() - float(stored_at)) > float(ttl):
            return None
    return entry.get("payload")


def put(endpoint: str, params: Any, payload: dict[str, Any], ttl_s: Optional[float]) -> None:
    """Atomically write a cache entry. ``ttl_s=None`` means permanent."""
    key = make_key(endpoint, params)
    path = _path_for(endpoint, key)
    entry = {"stored_at": time.time(), "ttl_s": ttl_s, "payload": payload}
    # Atomic write to survive crashes mid-write.
    fd, tmp_name = tempfile.mkstemp(prefix=".veedcrawl-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(entry, fp, ensure_ascii=False)
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup; never raise from cache writes.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def clear() -> None:
    """Remove every cached entry. Useful for tests."""
    root = cache_root()
    for path in root.rglob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass
