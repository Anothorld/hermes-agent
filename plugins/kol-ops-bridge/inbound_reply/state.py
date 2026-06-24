"""Poller state, cross-process lock, and Console dedup sidecar."""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import fcntl
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_PATH = _HERMES_HOME / "kol-ops-bridge" / "poller_state.json"
_LOCK_PATH = _STATE_PATH.with_suffix(".lock")


def _default_console_db_path() -> Path:
    explicit = os.environ.get("KOC_DB_PATH")
    if explicit:
        return Path(explicit).expanduser()
    # Console is shared across Hermes profiles; do not tie it to profile HERMES_HOME.
    return Path.home() / ".hermes" / "kol-ops-console" / "app.db"


_CONSOLE_DB_PATH = _default_console_db_path()
_SEEN_CAP = 2000


def retry_backoff_bucket_key(env: str) -> str:
    return f"retry_backoff_{env}"


def retry_failures_key(env: str) -> str:
    return f"retry_failures_{env}"


def gateway_retry_base_sec() -> int:
    return max(15, int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_RETRY_BASE_SEC", "60")))


def gateway_retry_max_sec() -> int:
    return max(gateway_retry_base_sec(), int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_RETRY_MAX_SEC", "3600")))


def gateway_only_retries_key(env: str) -> str:
    return f"gateway_only_retries_{env}"


def gateway_only_retry_max() -> int:
    return max(1, int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_ONLY_RETRY_MAX", "8")))


def gateway_only_retry_count(
    state: dict[str, Any], *, env: str, message_id: str,
) -> int:
    bucket = state.get(gateway_only_retries_key(env), {})
    if not isinstance(bucket, dict):
        return 0
    try:
        return int(bucket.get(message_id, 0))
    except (TypeError, ValueError):
        return 0


def gateway_only_retry_exceeded(
    state: dict[str, Any], *, env: str, message_id: str,
) -> bool:
    return gateway_only_retry_count(state, env=env, message_id=message_id) >= gateway_only_retry_max()


def record_gateway_only_dispatch(
    state: dict[str, Any], *, env: str, message_id: str,
) -> int:
    bucket = state.setdefault(gateway_only_retries_key(env), {})
    if not isinstance(bucket, dict):
        bucket = {}
        state[gateway_only_retries_key(env)] = bucket
    count = gateway_only_retry_count(state, env=env, message_id=message_id) + 1
    bucket[message_id] = count
    return count


def clear_gateway_only_retries(
    state: dict[str, Any], *, env: str, message_id: str,
) -> None:
    bucket = state.get(gateway_only_retries_key(env))
    if isinstance(bucket, dict):
        bucket.pop(message_id, None)


def retry_not_before(state: dict[str, Any], *, env: str, message_id: str) -> float:
    bucket = state.get(retry_backoff_bucket_key(env), {})
    if not isinstance(bucket, dict):
        return 0.0
    try:
        return float(bucket.get(message_id) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def record_retry_backoff(state: dict[str, Any], *, env: str, message_id: str) -> None:
    failures_bucket = state.setdefault(retry_failures_key(env), {})
    if not isinstance(failures_bucket, dict):
        failures_bucket = {}
        state[retry_failures_key(env)] = failures_bucket
    failures = int(failures_bucket.get(message_id, 0)) + 1
    failures_bucket[message_id] = failures
    delay = min(gateway_retry_max_sec(), gateway_retry_base_sec() * (2 ** min(failures - 1, 6)))
    backoff_bucket = state.setdefault(retry_backoff_bucket_key(env), {})
    if not isinstance(backoff_bucket, dict):
        backoff_bucket = {}
        state[retry_backoff_bucket_key(env)] = backoff_bucket
    backoff_bucket[message_id] = time.time() + delay


def clear_retry_backoff(state: dict[str, Any], *, env: str, message_id: str) -> None:
    for key in (retry_backoff_bucket_key(env), retry_failures_key(env)):
        bucket = state.get(key)
        if isinstance(bucket, dict):
            bucket.pop(message_id, None)
    clear_gateway_only_retries(state, env=env, message_id=message_id)


def state_path() -> Path:
    return _STATE_PATH


def load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("poller_state unreadable; starting fresh")
        return {}


def save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_STATE_PATH)


def register_console_run(
    *,
    campaign_id: str | None,
    env: str,
    run_id: str,
    session_id: str,
) -> None:
    if not campaign_id or not run_id:
        return
    db_path = _default_console_db_path()
    try:
        if not db_path.exists():
            log.warning(
                "console run-registry skipped: db missing at %s (set KOC_DB_PATH?)",
                db_path,
            )
            return
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """INSERT OR IGNORE INTO product_campaign_runs
                        (campaign_id, env, run_id, kind, session_id, started_at)
                    VALUES (?,?,?,?,?,?)""",
                (campaign_id, env, run_id, "reply", session_id, now),
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("console run-registry insert skipped: %s", exc)


def _console_db_connect() -> sqlite3.Connection | None:
    try:
        if not _CONSOLE_DB_PATH.exists():
            return None
        return sqlite3.connect(str(_CONSOLE_DB_PATH), timeout=5.0, isolation_level=None)
    except sqlite3.Error:
        return None


def global_message_seen(*, env: str, message_id: str) -> bool:
    conn = _console_db_connect()
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM gmail_poller_global_seen WHERE env=? AND message_id=? LIMIT 1",
            (env, message_id),
        ).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        log.warning("global seen lookup skipped: %s", exc)
        return False
    finally:
        conn.close()


def record_global_message_seen(
    *, env: str, message_id: str, mailbox_user_id: int,
) -> None:
    conn = _console_db_connect()
    if conn is None:
        return
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """INSERT OR IGNORE INTO gmail_poller_global_seen
                    (env, message_id, mailbox_user_id, seen_at)
                VALUES (?,?,?,?)""",
            (env, message_id, int(mailbox_user_id), now),
        )
        conn.execute(
            """INSERT INTO gmail_poller_watermarks (user_id, last_message_id, updated_at)
                VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_message_id=excluded.last_message_id,
                    updated_at=excluded.updated_at""",
            (int(mailbox_user_id), message_id, now),
        )
    except sqlite3.Error as exc:
        log.warning("global seen / watermark write skipped: %s", exc)
    finally:
        conn.close()


_THREAD_LOCK_DEPTH = threading.local()


def _state_lock_depth() -> int:
    return int(getattr(_THREAD_LOCK_DEPTH, "depth", 0) or 0)


@contextlib.contextmanager
def state_lock(*, blocking: bool = True) -> Iterator[None]:
    """Exclusive lock around a full run_once cycle (Megan duplicate prevention).

    Reentrant within the same thread so ``process_message`` can update retry
    counters while ``run_once`` already holds the cross-process flock.
    """
    depth = _state_lock_depth()
    if depth > 0:
        _THREAD_LOCK_DEPTH.depth = depth + 1
        try:
            yield
        finally:
            _THREAD_LOCK_DEPTH.depth = depth
        return

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(_LOCK_PATH, "a+")
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError(
                    "another inbound reply dispatcher run is in progress "
                    f"(lock={_LOCK_PATH})"
                ) from exc
            raise
        _THREAD_LOCK_DEPTH.depth = 1
        yield
    finally:
        try:
            _THREAD_LOCK_DEPTH.depth = 0
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def trim_seen(seen: set[str]) -> list[str]:
    return sorted(seen)[-_SEEN_CAP:]
