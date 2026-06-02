"""Aggregate learning dashboard payload for Console / operators."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from . import learning_distill
from . import learning_job_store as job_store
from . import learning_promote
from . import learning_store
from . import policies as pol


def _count_approved_style_proposals(conn: sqlite3.Connection, *, env: str) -> int:
    rows = conn.execute(
        """SELECT fact_value FROM kol_facts_latest
            WHERE fact_namespace='approval'
              AND fact_key=?
              AND env=?""",
        (learning_store.STYLE_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    count = 0
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(val, dict) and val.get("decision") == "approved":
            count += 1
    return count


def build_learning_overview(
    conn: sqlite3.Connection,
    *,
    env: str,
    runs_limit: int = 25,
) -> dict[str, Any]:
    """Single-call snapshot for the autonomous learning Console page."""
    threshold = learning_store.style_learning_batch_size()
    events = learning_store.list_learning_events(
        conn, env=env, event_types=("draft_edit_learning",), limit=200,
    )
    consumed = learning_distill.list_consumed_edit_event_ids(conn, env=env)
    fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
    edited = [
        e for e in fresh if (e.get("payload") or {}).get("was_edited")
    ]

    pending_proposals: list[dict[str, Any]] = []
    for pending in learning_distill.list_pending_style_proposals(conn, env=env):
        val = pending.get("value") or {}
        pending_proposals.append({
            "scope": pending.get("scope") or val.get("scope"),
            "owner_user_id": pending.get("owner_user_id"),
            "identity_id": pending.get("identity_id"),
            "sample_count": val.get("sample_count"),
            "batch_threshold": val.get("batch_threshold"),
            "llm_used": val.get("llm_used"),
            "captured_at": pending.get("captured_at"),
        })

    policy_versions: dict[str, Any] = {}
    for scope in (
        learning_store.REJECT_LEARNING_SCOPE,
        learning_store.REPLY_STRATEGY_SCOPE,
        "company_style",
    ):
        policy_env = env if scope in pol.ENV_SCOPED_POLICIES else None
        row = pol.get_policy(conn, scope=scope, env=policy_env)
        policy_versions[scope] = {
            "version": (row or {}).get("version"),
            "updated_at": (row or {}).get("updated_at"),
            "content_chars": len((row or {}).get("content_md") or ""),
        }

    promote_eligibility: list[dict[str, Any]] = []
    for goal in learning_promote.PROMOTABLE_GOALS:
        assess = learning_promote.select_promotable_strategy(
            conn, env=env, goal=goal,
        )
        promote_eligibility.append({
            "goal": assess["goal"],
            "skill": assess["skill"],
            "eligible": assess["eligible"],
            "reason": assess["reason"],
            "approvals": assess["approvals"],
            "policy_versions_with_goal": assess["approvals"],
            "age_days": assess["age_days"],
        })

    last_runs = job_store.list_runs(conn, env=env, limit=runs_limit)
    summary = {"ok": 0, "skipped": 0, "error": 0, "running": 0}
    for run in last_runs:
        st = str(run.get("status") or "")
        if st in summary:
            summary[st] += 1

    disabled_raw = os.environ.get("KOL_LEARNING_JOBS_DISABLED", "").strip().lower()
    jobs_disabled = disabled_raw in ("1", "true", "yes", "on")

    return {
        "env": env,
        "jobs_disabled": jobs_disabled,
        "style_in_hints": learning_store._env_flag("KOL_STYLE_IN_HINTS", default=True),
        "batch_threshold": threshold,
        "edit_stats": {
            "total_events": len(events),
            "unconsumed": len(fresh),
            "edited_unconsumed": len(edited),
            "consumed": len(consumed),
            "ready_for_distill": len(edited) >= threshold,
        },
        "pending_style_proposals": pending_proposals,
        "approved_style_proposals": _count_approved_style_proposals(conn, env=env),
        "promote_metric_note": (
            "promote_eligibility.approvals counts reply_strategy policy versions "
            "containing the goal section, not Console approval rows. "
            "approved_style_proposals counts Console-approved style batches."
        ),
        "policy_versions": policy_versions,
        "promote_eligibility": promote_eligibility,
        "last_runs": last_runs,
        "run_summary": summary,
    }


__all__ = ["build_learning_overview"]
