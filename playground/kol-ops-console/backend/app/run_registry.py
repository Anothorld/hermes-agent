"""Helpers for ``product_campaign_runs``.

Every place that spawns a gateway run for a tracked campaign (campaign
launch, reply dispatch, draft preview, escalation resume, draft refine)
should register the new run_id here so the transcript panel can multiplex
all live SSE feeds for a single campaign.

For runs that must not double-fire while one is still working (currently
preview-draft and refine — both write the same ``approval.reply_draft``
fact), the caller also passes a ``dedup_key`` and consults
``get_inflight_run()`` BEFORE starting the gateway run. The console has
no completion callback, so ``ended_at`` is never written; the in-flight
check uses a TTL on ``started_at`` instead (default 5 min).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Iterable, Literal, Optional

RunKind = Literal["outreach", "reply", "draft", "resume", "refine"]

# How long a registered run is considered "in flight" for dedup purposes.
# Preview-draft / refine usually complete in 30-60 s; we err on the long
# side so a slow LLM or a queued run still blocks a duplicate trigger,
# but past this window the operator can re-fire if no fact appeared.
INFLIGHT_TTL_SECONDS = 300


def register_run(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    run_id: str,
    kind: RunKind,
    session_id: str | None = None,
    dedup_key: str | None = None,
) -> None:
    """Insert a row into product_campaign_runs. No-op on duplicate run_id."""
    if not run_id:
        return
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT OR IGNORE INTO product_campaign_runs
                (campaign_id, env, run_id, kind, session_id, dedup_key, started_at)
            VALUES (?,?,?,?,?,?,?)""",
        (campaign_id, env, run_id, kind, session_id, dedup_key, now),
    )


def mark_run_ended(conn: sqlite3.Connection, *, run_id: str) -> None:
    if not run_id:
        return
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE product_campaign_runs SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
        (now, run_id),
    )


def get_inflight_run(
    conn: sqlite3.Connection,
    *,
    dedup_key: str,
    ttl_seconds: int = INFLIGHT_TTL_SECONDS,
) -> Optional[dict]:
    """Return the most recent run matching ``dedup_key`` whose
    ``started_at`` falls within the in-flight TTL, or None.

    Callers (preview_draft, refine) use this to 409 a duplicate trigger.
    ``ended_at`` is intentionally ignored — the console has no completion
    callback, so a NULL ``ended_at`` is the norm, not the in-flight
    signal. We treat anything newer than ``now - ttl_seconds`` as still
    potentially running.
    """
    if not dedup_key:
        return None
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc)
        - _dt.timedelta(seconds=ttl_seconds)
    ).isoformat(timespec="seconds")
    row = conn.execute(
        """SELECT run_id, kind, session_id, started_at, ended_at
             FROM product_campaign_runs
            WHERE dedup_key=? AND started_at>=?
            ORDER BY started_at DESC
            LIMIT 1""",
        (dedup_key, cutoff),
    ).fetchone()
    return dict(row) if row else None


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


def list_open_runs_for_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    max_age_hours: int = 24,
) -> list[dict]:
    """Return registered runs with ``ended_at IS NULL`` started within
    ``max_age_hours``. Used by ``_sync_run_states`` to poll terminal state
    and write ``ended_at`` for every live run on the campaign, not just the
    one referenced by ``product_campaigns.run_id``.

    The age guard keeps the poll cost bounded; runs that never received a
    terminal signal and predate the cutoff are abandoned (the gateway
    evicted them long ago anyway).
    """
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc)
        - _dt.timedelta(hours=max_age_hours)
    ).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT run_id, kind, session_id, dedup_key, started_at, ended_at
             FROM product_campaign_runs
            WHERE campaign_id=? AND env=? AND ended_at IS NULL
              AND started_at>=?
            ORDER BY started_at DESC""",
        (campaign_id, env, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


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
