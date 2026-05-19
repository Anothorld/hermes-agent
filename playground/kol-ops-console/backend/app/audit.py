"""Audit logging helper."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Optional


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_user_id: Optional[int],
    action: str,
    target: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, payload_json, ts) VALUES (?,?,?,?,?)",
        (
            actor_user_id,
            action,
            target,
            json.dumps(payload or {}, ensure_ascii=False),
            _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        ),
    )
