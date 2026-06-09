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
    return _HERMES_HOME / "kol-ops-console" / "app.db"


_CONSOLE_DB_PATH = _default_console_db_path()
_SEEN_CAP = 2000


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
    try:
        if not _CONSOLE_DB_PATH.exists():
            return
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        conn = sqlite3.connect(str(_CONSOLE_DB_PATH), timeout=5.0, isolation_level=None)
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


@contextlib.contextmanager
def state_lock(*, blocking: bool = True) -> Iterator[None]:
    """Exclusive lock around a full run_once cycle (Megan duplicate prevention)."""
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
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def trim_seen(seen: set[str]) -> list[str]:
    return sorted(seen)[-_SEEN_CAP:]
