"""Aggregate learning dashboard payload for Console / operators."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from . import learning_discovery
from . import learning_distill
from . import discovery_decision_learning as ddl
from . import learning_job_store as job_store
from . import learning_outcome
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
    # Edits already batched into a pending proposal are not available for the
    # next distill until that proposal is approved (consumed) or rejected.
    reserved_ids = learning_distill.pending_style_reserved_event_ids(conn, env=env)
    edited_available = [
        e for e in edited
        if int(e.get("id") or 0) not in reserved_ids
    ]
    edited_queued = len(edited) - len(edited_available)

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

    # Collaboration outcome learning (result-level retros + pending synthesis).
    outcome_retros = learning_outcome.list_outcome_retro_events(conn, env=env, limit=200)
    outcome_consumed = learning_outcome.list_consumed_outcome_event_ids(conn, env=env)
    outcome_fresh = [
        e for e in outcome_retros if int(e.get("id") or 0) not in outcome_consumed
    ]
    outcome_fresh = learning_store.filter_events_within_days(
        outcome_fresh, learning_store.learning_window_days(),
    )
    outcome_reserved = learning_outcome.pending_outcome_reserved_event_ids(conn, env=env)
    outcome_available = [
        e for e in outcome_fresh
        if int(e.get("id") or 0) not in outcome_reserved
    ]
    outcome_queued = len(outcome_fresh) - len(outcome_available)
    outcome_grouped = learning_outcome._group_retros_by_segment(outcome_available)
    outcome_met = False
    outcome_gate: dict[str, Any] = {
        "total": len(outcome_available),
        "failures": sum(
            1 for e in outcome_available
            if str((e.get("payload") or {}).get("outcome_class") or "") == "failure"
        ),
        "batch_size": learning_outcome.outcome_batch_size(),
        "min_failures": learning_outcome.outcome_min_failures(),
    }
    for _seg, seg_events in outcome_grouped.items():
        met, gate = learning_outcome._outcome_threshold_met(seg_events)
        if met:
            outcome_met = True
            outcome_gate = gate
            break
    by_class: dict[str, int] = {"failure": 0, "success": 0, "partial": 0}
    for ev in outcome_retros:
        cls = str((ev.get("payload") or {}).get("outcome_class") or "partial")
        by_class[cls] = by_class.get(cls, 0) + 1
    outcome_pending = learning_outcome.find_pending_outcome_proposal(conn, env=env)
    outcome_stats = {
        "total_retros": len(outcome_retros),
        "fresh_retros": len(outcome_fresh),
        "fresh_available": len(outcome_available),
        "fresh_queued_in_pending": outcome_queued,
        "by_class": by_class,
        "ready_for_synthesis": outcome_met,
        "has_pending_proposal": outcome_pending is not None,
        **outcome_gate,
    }

    # Shortlist-decision learning (discover loop): per SPU/category batch
    # progress toward the next learned-criteria proposal.
    try:
        discovery_stats = learning_discovery.discovery_overview_stats(conn, env=env)
    except Exception:  # noqa: BLE001 — dashboard must render without this block
        discovery_stats = {"error": "discovery stats unavailable"}

    policy_versions: dict[str, Any] = {}
    for scope in (
        learning_store.REJECT_LEARNING_SCOPE,
        learning_store.REPLY_STRATEGY_SCOPE,
        learning_store.OUTCOME_STRATEGY_SCOPE,
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
            "scope": learning_store.REPLY_STRATEGY_SCOPE,
            "goal": assess["goal"],
            "skill": assess["skill"],
            "eligible": assess["eligible"],
            "reason": assess["reason"],
            "approvals": assess["approvals"],
            "policy_versions_with_goal": assess["approvals"],
            "age_days": assess["age_days"],
        })
    promote_outcome_eligibility: list[dict[str, Any]] = []
    for goal in learning_promote.PROMOTABLE_GOALS:
        assess = learning_promote.select_promotable_strategy(
            conn,
            env=env,
            goal=goal,
            scope=learning_store.OUTCOME_STRATEGY_SCOPE,
        )
        promote_outcome_eligibility.append({
            "scope": learning_store.OUTCOME_STRATEGY_SCOPE,
            "goal": assess["goal"],
            "skill": assess["skill"],
            "eligible": assess["eligible"],
            "reason": assess["reason"],
            "approvals": assess["approvals"],
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

    # Convergence metric: is the operator editing AI drafts less over time?
    trend = learning_store.edit_distance_trend(conn, env=env, days=90, bucket="week")
    style_approval_markers = learning_distill.list_style_approval_markers(
        conn, env=env, days=90,
    )
    channel_trends = {
        "edits": trend,
        "rejects": learning_store.event_volume_trend(
            conn, env=env, event_type="draft_rejected_learning", days=90, bucket="week",
        ),
        "outcome_retros": learning_store.event_volume_trend(
            conn, env=env, event_type=learning_outcome.OUTCOME_LEARNING_EVENT,
            days=90, bucket="week",
        ),
        "shortlist_decisions": learning_store.event_volume_trend(
            conn, env=env, event_type=ddl.SHORTLIST_DECISION_EVENT,
            days=90, bucket="week",
        ),
    }
    # Regression guard: flag when recent edits got LARGER after learning (a bad
    # batch may have degraded the policy → consider rolling back).
    try:
        alert_delta = float(
            os.environ.get("KOL_LEARNING_CONVERGENCE_ALERT_DELTA", "0.05")
        )
    except ValueError:
        alert_delta = 0.05
    rvp = trend.get("recent_vs_prior") or {}
    last_marker_at = None
    if style_approval_markers:
        last_marker_at = max(
            str(m.get("at") or "") for m in style_approval_markers
        ) or None
    after_approval = learning_store.edit_distance_since_last_style_approval(
        conn,
        env=env,
        last_approval_at=last_marker_at,
        days=90,
    )
    guard_basis = "recent_vs_prior"
    delta = rvp.get("delta")
    if after_approval.get("usable"):
        guard_basis = "after_last_approval"
        delta = after_approval.get("delta")
    convergence_alert = {
        "worsening": bool(delta is not None and delta > alert_delta),
        "delta": delta,
        "threshold": alert_delta,
        "guard_basis": guard_basis,
        "after_last_approval": after_approval,
        "recent_vs_prior": rvp,
        "hint": (
            "自最近一次学习批准后，操作员编辑幅度上升，可能该批 policy 反而变差；可在 policy 历史回滚上一版。"
            if guard_basis == "after_last_approval"
            and (delta is not None and delta > alert_delta)
            else (
                "最近一周编辑幅度高于此前各周平均，可能某次批准的 policy 反而变差；可在 policy 历史回滚上一版。"
                if (delta is not None and delta > alert_delta)
                else ""
            )
        ),
    }

    edit_stats_by_scope = learning_distill.build_edit_stats_by_scope(
        conn, env=env, threshold=threshold,
    )

    return {
        "env": env,
        "jobs_disabled": jobs_disabled,
        "edit_distance_trend": trend,
        "style_approval_markers": style_approval_markers,
        "channel_trends": channel_trends,
        "convergence_alert": convergence_alert,
        "outcome_learning": outcome_stats,
        "discovery_learning": discovery_stats,
        "style_in_hints": learning_store._env_flag("KOL_STYLE_IN_HINTS", default=True),
        "batch_threshold": threshold,
        "edit_stats": {
            "total_events": len(events),
            "unconsumed": len(fresh),
            "edited_unconsumed": len(edited),
            "edited_available": len(edited_available),
            "edited_queued_in_pending": edited_queued,
            "consumed": len(consumed),
            "ready_for_distill": len(edited_available) >= threshold,
        },
        "edit_stats_by_scope": edit_stats_by_scope,
        "pending_style_proposals": pending_proposals,
        "approved_style_proposals": _count_approved_style_proposals(conn, env=env),
        "promote_metric_note": (
            "promote_eligibility.approvals counts reply_strategy policy versions "
            "containing the goal section, not Console approval rows. "
            "approved_style_proposals counts Console-approved style batches."
        ),
        "policy_versions": policy_versions,
        "promote_eligibility": promote_eligibility,
        "promote_outcome_eligibility": promote_outcome_eligibility,
        "last_runs": last_runs,
        "run_summary": summary,
    }


__all__ = ["build_learning_overview"]
