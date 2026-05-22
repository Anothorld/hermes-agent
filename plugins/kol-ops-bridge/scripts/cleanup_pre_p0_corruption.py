#!/usr/bin/env python3
"""One-shot cleanup for data corrupted by the pre-P0 bugs.

Two data-corruption patterns existed before the Phase F fixes landed:

1. **Lost approval payloads.** ``_approve_or_reject`` discarded any
   non-dict previous fact value when stamping the operator decision.
   So a scalar payload written by the resumer (e.g.
   ``approval.outreach_missing_public_email_resolution =
   "use_campaign_test_mode_to_only"``) ended up as just
   ``{"decision": "approved", "decided_by": "..."}``, with the
   original proposal text gone. After the fix, *new* approvals
   preserve the prior value under ``"value"`` — but the rows already
   in CAL still need to be back-filled.

2. **Stuck ``answered`` escalations.** ``open_escalation`` did not
   transition the parent when a follow-up escalation was opened, and
   ``cal.resolve_escalation`` did not auto-move ``answered`` → final
   state when the operator's resume path led to a child escalation
   *without* ``parent_escalation_id``. Result: escalations like
   ``#3`` of the TS8136 run sit in ``answered`` forever.

This script is **idempotent**, **dry-run by default**, and only
touches rows that match the corruption fingerprint above.

Usage::

    # See what would change (no writes):
    python plugins/kol-ops-bridge/scripts/cleanup_pre_p0_corruption.py

    # Actually apply the fixes:
    python plugins/kol-ops-bridge/scripts/cleanup_pre_p0_corruption.py --apply

    # Restrict to a single campaign / env:
    python plugins/kol-ops-bridge/scripts/cleanup_pre_p0_corruption.py \\
        --apply --campaign-id TS8136-TEST-20260521 --env TEST

Verify in advance with ``--apply --dry-run`` if you want to dry-run
twice with the apply gate on.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _default_db_path() -> Path:
    p = os.environ.get("HERMES_KOL_OPS_CAL_DB")
    if p:
        return Path(os.path.expanduser(p))
    return Path(os.path.expanduser("~/.hermes/kol-ops-bridge/cal.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------


def find_lost_approval_values(
    conn: sqlite3.Connection,
    *,
    campaign_id: Optional[str],
    env: Optional[str],
) -> list[dict[str, Any]]:
    """Return rows where the latest approval fact_value is a dict that
    has ``decision`` set but no ``value`` key, AND a prior row of the
    same fact_key had a non-dict (scalar/list) value to restore.
    """
    where = ["fact_namespace = 'approval'"]
    params: list[Any] = []
    if campaign_id:
        where.append("campaign_id = ?")
        params.append(campaign_id)
    if env:
        where.append("env = ?")
        params.append(env)
    rows = conn.execute(
        f"""SELECT id, identity_id, campaign_id, fact_key, fact_value, env
              FROM kol_facts_latest
             WHERE {' AND '.join(where)}
          ORDER BY id""",
        params,
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            val = json.loads(r["fact_value"])
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict):
            continue
        if "decision" not in val:
            continue
        if "value" in val:
            continue
        # Look for an earlier row of the same fact_key whose value was
        # NOT itself a decision-stamped dict.
        prior = conn.execute(
            """SELECT fact_value FROM kol_facts
                WHERE id < ?
                  AND identity_id = ?
                  AND COALESCE(campaign_id, '') = COALESCE(?, '')
                  AND fact_key = ?
                  AND env = ?
              ORDER BY id DESC LIMIT 5""",
            (r["id"], r["identity_id"], r["campaign_id"], r["fact_key"], r["env"]),
        ).fetchall()
        recovered: Any = None
        for p in prior:
            try:
                pv = json.loads(p["fact_value"])
            except (TypeError, ValueError):
                pv = p["fact_value"]
            if isinstance(pv, dict) and "decision" in pv:
                continue
            recovered = pv
            break
        if recovered is None and prior:
            # Bare string scalars are stored verbatim — fact_value may
            # not be JSON. Use the raw string as the recovered payload.
            recovered = prior[0]["fact_value"]
        if recovered is None:
            continue
        out.append({
            "id": r["id"],
            "identity_id": r["identity_id"],
            "campaign_id": r["campaign_id"],
            "fact_key": r["fact_key"],
            "env": r["env"],
            "current_value": val,
            "recovered_value": recovered,
        })
    return out


def restore_approval_value(
    conn: sqlite3.Connection, hit: dict[str, Any], *, dry_run: bool,
) -> None:
    """Append a new fact row that merges ``recovered`` into the latest
    decision-stamped dict so the resumer can read what was approved.
    We do NOT mutate the existing row — kol_facts is append-only.
    """
    merged = dict(hit["current_value"])
    merged["value"] = hit["recovered_value"]
    payload = json.dumps(merged, ensure_ascii=False, sort_keys=True)
    if dry_run:
        return
    conn.execute(
        """INSERT INTO kol_facts
           (identity_id, campaign_id, fact_namespace, fact_key,
            fact_value, source, source_event_id, captured_at, env)
           VALUES (?, ?, 'approval', ?, ?, ?, NULL, ?, ?)""",
        (hit["identity_id"], hit["campaign_id"], hit["fact_key"],
         payload, "cleanup_pre_p0_corruption", _now(), hit["env"]),
    )


# ---------------------------------------------------------------------------


def find_stuck_answered_escalations(
    conn: sqlite3.Connection,
    *,
    campaign_id: Optional[str],
    env: Optional[str],
    stale_after_days: int = 7,
) -> list[dict[str, Any]]:
    """Return escalations in ``state='answered'`` whose ``updated_at``
    is older than ``stale_after_days`` and where a *later* escalation
    for the same (identity, campaign, env) exists — strong signal the
    resumer moved on without closing the parent.
    """
    cutoff = (datetime.now(timezone.utc).timestamp()
              - stale_after_days * 86400.0)
    where = ["state = 'answered'"]
    params: list[Any] = []
    if campaign_id:
        where.append("campaign_id = ?")
        params.append(campaign_id)
    if env:
        where.append("env = ?")
        params.append(env)
    rows = conn.execute(
        f"""SELECT id, identity_id, campaign_id, goal, env, updated_at
              FROM kol_escalations
             WHERE {' AND '.join(where)}
          ORDER BY id""",
        params,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(
                r["updated_at"].replace("Z", "+00:00"),
            ).timestamp()
        except (AttributeError, ValueError):
            continue
        if ts > cutoff:
            continue
        # Was a follow-up escalation opened later for the same context?
        sibling = conn.execute(
            """SELECT 1 FROM kol_escalations
                WHERE id > ?
                  AND COALESCE(identity_id, -1) = COALESCE(?, -1)
                  AND COALESCE(campaign_id, '') = COALESCE(?, '')
                  AND env = ?
                LIMIT 1""",
            (r["id"], r["identity_id"], r["campaign_id"], r["env"]),
        ).fetchone()
        if not sibling:
            continue
        out.append({
            "id": r["id"],
            "identity_id": r["identity_id"],
            "campaign_id": r["campaign_id"],
            "goal": r["goal"],
            "env": r["env"],
            "updated_at": r["updated_at"],
        })
    return out


def close_stuck_answered(
    conn: sqlite3.Connection, hit: dict[str, Any], *, dry_run: bool,
) -> None:
    """Transition the parent to ``resolved`` (operator's answer was
    already applied — the follow-up escalation owns the new question).
    We never touch the goal_state; the follow-up escalation already
    owns the ``blocked`` state.
    """
    if dry_run:
        return
    now = _now()
    conn.execute(
        """UPDATE kol_escalations
              SET state = 'resolved', updated_at = ?
            WHERE id = ? AND state = 'answered'""",
        (now, hit["id"]),
    )


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(_default_db_path()),
                        help="Path to cal.db (default: ~/.hermes/kol-ops-bridge/cal.db)")
    parser.add_argument("--campaign-id", default=None,
                        help="Restrict to one campaign_id")
    parser.add_argument("--env", default=None, choices=["TEST", "LIVE"],
                        help="Restrict to one env")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes (default is dry-run).")
    parser.add_argument("--stale-after-days", type=int, default=7,
                        help="Only touch ``answered`` escalations older than this (default 7)")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"error: db not found: {db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        lost = find_lost_approval_values(conn, campaign_id=args.campaign_id,
                                         env=args.env)
        print(f"\n[1/2] Lost-approval-value rows: {len(lost)}")
        for h in lost:
            print(f"  - id={h['id']} env={h['env']} "
                  f"campaign={h['campaign_id']} "
                  f"key={h['fact_key']}")
            print(f"      recovered: {h['recovered_value']!r}")
            restore_approval_value(conn, h, dry_run=not args.apply)

        stuck = find_stuck_answered_escalations(
            conn, campaign_id=args.campaign_id, env=args.env,
            stale_after_days=args.stale_after_days,
        )
        print(f"\n[2/2] Stuck ``answered`` escalations (>= "
              f"{args.stale_after_days}d old, sibling-followed): "
              f"{len(stuck)}")
        for h in stuck:
            print(f"  - escalation #{h['id']} env={h['env']} "
                  f"campaign={h['campaign_id']} goal={h['goal']} "
                  f"updated_at={h['updated_at']} -> resolved")
            close_stuck_answered(conn, h, dry_run=not args.apply)

        if args.apply:
            conn.commit()
            print("\nWrote changes.")
        else:
            print("\nDRY RUN — no changes written. Re-run with --apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
