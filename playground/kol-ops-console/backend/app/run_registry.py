"""Helpers for ``product_campaign_runs``.

Every place that spawns a gateway run for a tracked campaign (campaign
launch, reply dispatch, draft preview, escalation resume) should register
the new run_id here so the transcript panel can multiplex all live SSE
feeds for a single campaign.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Iterable, Literal

RunKind = Literal["outreach", "reply", "draft", "resume", "refine"]


def register_run(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    run_id: str,
    kind: RunKind,
    session_id: str | None = None,
) -> None:
    """Insert a row into product_campaign_runs. No-op on duplicate run_id."""
    if not run_id:
        return
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT OR IGNORE INTO product_campaign_runs
                (campaign_id, env, run_id, kind, session_id, started_at)
            VALUES (?,?,?,?,?,?)""",
        (campaign_id, env, run_id, kind, session_id, now),
    )


def mark_run_ended(conn: sqlite3.Connection, *, run_id: str) -> None:
    if not run_id:
        return
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE product_campaign_runs SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
        (now, run_id),
    )


def list_runs_for_campaign(
    conn: sqlite3.Connection, *, campaign_id: str, env: str, limit: int = 20
) -> list[dict]:
    """Most recent runs first. Returns rows shaped for the FE registry."""
    rows = conn.execute(
        """SELECT run_id, kind, session_id, started_at, ended_at
             FROM product_campaign_runs
            WHERE campaign_id=? AND env=?
            ORDER BY started_at DESC
            LIMIT ?""",
        (campaign_id, env, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_run_ids_for_campaign(
    conn: sqlite3.Connection, *, campaign_id: str, env: str, limit: int = 20
) -> list[str]:
    return [r["run_id"] for r in list_runs_for_campaign(
        conn, campaign_id=campaign_id, env=env, limit=limit
    )]


def merge_legacy_run_id(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    legacy_run_id: str | None,
    legacy_kind: RunKind = "outreach",
) -> Iterable[str]:
    """Best-effort backfill for campaigns whose only run_id lives on
    ``product_campaigns``. Adds it to the registry on first call so the
    multi-run feed includes it. Returns the registry's run_ids after backfill."""
    if legacy_run_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=legacy_run_id,
            kind=legacy_kind,
        )
    return list_run_ids_for_campaign(conn, campaign_id=campaign_id, env=env)
