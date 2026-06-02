"""Persist learning cron job runs for full audit / traceability."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

JOB_STATUS_OK = "ok"
JOB_STATUS_SKIPPED = "skipped"
JOB_STATUS_ERROR = "error"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(
    conn: sqlite3.Connection,
    *,
    job_name: str,
    env: Optional[str],
    triggered_by: str,
    input_payload: Optional[dict[str, Any]] = None,
) -> int:
    """Insert a running row; returns ``run_id``."""
    cur = conn.execute(
        """INSERT INTO kol_learning_job_runs
              (job_name, env, status, triggered_by, started_at, finished_at,
               duration_ms, input_json, output_json, error_message)
           VALUES (?, ?, 'running', ?, ?, NULL, NULL, ?, '{}', NULL)""",
        (
            job_name,
            env,
            triggered_by,
            _now(),
            json.dumps(input_payload or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    output: Optional[dict[str, Any]] = None,
    error_message: Optional[str] = None,
    started_at: Optional[str] = None,
) -> dict[str, Any]:
    """Mark a run complete and return the final row."""
    finished = _now()
    duration_ms: Optional[int] = None
    if started_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration_ms = int((end - start).total_seconds() * 1000)
        except ValueError:
            duration_ms = None
    conn.execute(
        """UPDATE kol_learning_job_runs
              SET status=?, finished_at=?, duration_ms=?,
                  output_json=?, error_message=?
            WHERE id=?""",
        (
            status,
            finished,
            duration_ms,
            json.dumps(output or {}, ensure_ascii=False),
            error_message,
            run_id,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM kol_learning_job_runs WHERE id=?", (run_id,),
    ).fetchone()
    out = dict(row) if row else {"id": run_id, "status": status}
    for key in ("input_json", "output_json"):
        try:
            out[key.replace("_json", "")] = json.loads(out.pop(key, "{}") or "{}")
        except (TypeError, ValueError):
            out[key.replace("_json", "")] = {}
    return out


def list_runs(
    conn: sqlite3.Connection,
    *,
    env: Optional[str] = None,
    job_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    where = ["1=1"]
    args: list[Any] = []
    if env is not None:
        where.append("env = ?")
        args.append(env)
    if job_name is not None:
        where.append("job_name = ?")
        args.append(job_name)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    sql = (
        "SELECT * FROM kol_learning_job_runs "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    )
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        for key in ("input_json", "output_json"):
            raw = d.pop(key, None)
            try:
                d[key.replace("_json", "")] = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                d[key.replace("_json", "")] = {}
        out.append(d)
    return out
