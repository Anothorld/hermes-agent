"""Scheduled learning jobs — autonomous distill + reconcile with audit trail."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Final, Optional

from . import cal
from . import discovery_decision_learning as discovery_dl
from . import learning_discovery
from . import learning_distill
from . import learning_job_store as job_store
from . import learning_outcome
from . import learning_store
from .gmail_client import GmailUnavailable
from .gmail_reconcile import backfill_edit_learning_all_mailboxes, run_reconcile_all_mailboxes

log = logging.getLogger(__name__)

# Process-wide guard so overlapping learning runs (capture every 15m vs a
# slow backfill, or capture vs nightly) never stack inside the bridge and
# starve the HTTP worker — the failure mode behind the capture-cron
# RemoteDisconnected / socket.timeout incidents. A run that arrives while
# another is in flight returns a fast "skipped" instead of contending.
# Serializing this scarce resource follows the gateway perf guardrail (§7).
_RUN_LOCK = threading.Lock()


# #region agent log
def _debug_log(message: str, data: dict) -> None:
    """Best-effort NDJSON debug log (session 60e90d). Never raises."""
    try:
        import json as _json
        import time as _time

        line = _json.dumps({
            "sessionId": "60e90d",
            "hypothesisId": "CAPTURE",
            "location": "learning_jobs.py",
            "message": message,
            "data": data,
            "timestamp": int(_time.time() * 1000),
        }, ensure_ascii=False)
        with open("/Users/arnold/agent_prj/.cursor/debug-60e90d.log", "a", encoding="utf-8") as _fh:
            _fh.write(line + "\n")
    except Exception:
        pass
# #endregion

# Autonomous cron learning always uses production data.
SCHEDULED_LEARNING_ENV: Final[str] = "LIVE"

JOB_RECONCILE_SENT: Final[str] = "reconcile_sent"
JOB_BACKFILL_EDIT_LEARNING: Final[str] = "backfill_edit_learning"
JOB_APPLY_REJECT_POLICY: Final[str] = "apply_reject_policy"
JOB_APPLY_EDIT_POLICY: Final[str] = "apply_edit_policy"
JOB_APPLY_PRICING_POLICY: Final[str] = "apply_pricing_calibration_policy"
JOB_AUTO_PRICING_CAMPAIGNS: Final[str] = "auto_pricing_campaigns"
JOB_SNAPSHOT_FACT_CORRECTIONS: Final[str] = "snapshot_fact_corrections"
JOB_SYNC_FAILURE_EXAMPLES: Final[str] = "sync_failure_examples"
JOB_CLASSIFIER_EVAL: Final[str] = "classifier_eval_deterministic"
JOB_APPLY_EDIT_USER_STYLE: Final[str] = "apply_edit_user_style"
JOB_ANALYZE_COLLAB_OUTCOME: Final[str] = "analyze_collab_outcome"
JOB_APPLY_OUTCOME_POLICY: Final[str] = "apply_outcome_policy"
JOB_APPLY_DISCOVERY_POLICY: Final[str] = "apply_discovery_policy"
JOB_MINE_DISCOVERY_TAGS: Final[str] = "mine_discovery_tags"

# Backward-compatible alias for older cron lines / docs.
JOB_AUTO_PRICING_TEST: Final[str] = "auto_pricing_test_campaigns"
_JOB_ALIASES: Final[dict[str, str]] = {
    JOB_AUTO_PRICING_TEST: JOB_AUTO_PRICING_CAMPAIGNS,
}

ALL_JOBS: Final[tuple[str, ...]] = (
    JOB_RECONCILE_SENT,
    JOB_BACKFILL_EDIT_LEARNING,
    JOB_ANALYZE_COLLAB_OUTCOME,
    JOB_APPLY_REJECT_POLICY,
    JOB_APPLY_EDIT_POLICY,
    JOB_APPLY_EDIT_USER_STYLE,
    JOB_APPLY_OUTCOME_POLICY,
    JOB_APPLY_DISCOVERY_POLICY,
    JOB_MINE_DISCOVERY_TAGS,
    JOB_APPLY_PRICING_POLICY,
    JOB_AUTO_PRICING_CAMPAIGNS,
    JOB_SNAPSHOT_FACT_CORRECTIONS,
    JOB_SYNC_FAILURE_EXAMPLES,
    JOB_CLASSIFIER_EVAL,
)

JOB_SUITES: Final[dict[str, tuple[str, ...]]] = {
    "capture": (
        JOB_RECONCILE_SENT,
        JOB_BACKFILL_EDIT_LEARNING,
        JOB_ANALYZE_COLLAB_OUTCOME,
    ),
    "distill": (
        JOB_APPLY_REJECT_POLICY,
        JOB_APPLY_EDIT_POLICY,
        JOB_APPLY_OUTCOME_POLICY,
        JOB_APPLY_DISCOVERY_POLICY,
    ),
    "pricing": (JOB_APPLY_PRICING_POLICY, JOB_AUTO_PRICING_CAMPAIGNS),
    "audit": (JOB_SNAPSHOT_FACT_CORRECTIONS, JOB_SYNC_FAILURE_EXAMPLES),
    "quality": (JOB_CLASSIFIER_EVAL,),
    "nightly": (
        JOB_ANALYZE_COLLAB_OUTCOME,
        JOB_APPLY_REJECT_POLICY,
        JOB_APPLY_EDIT_POLICY,
        JOB_APPLY_OUTCOME_POLICY,
        JOB_APPLY_DISCOVERY_POLICY,
        JOB_MINE_DISCOVERY_TAGS,
        JOB_APPLY_PRICING_POLICY,
        JOB_AUTO_PRICING_CAMPAIGNS,
        JOB_SNAPSHOT_FACT_CORRECTIONS,
        JOB_SYNC_FAILURE_EXAMPLES,
        JOB_CLASSIFIER_EVAL,
    ),
    "all": ALL_JOBS,
}


def require_scheduled_learning_env(env: str) -> str:
    """Reject non-LIVE env for autonomous learning (only LIVE data is meaningful)."""
    normalized = (env or "").strip().upper()
    if normalized != SCHEDULED_LEARNING_ENV:
        raise ValueError(
            f"scheduled learning jobs only run on {SCHEDULED_LEARNING_ENV}; got {env!r}",
        )
    return SCHEDULED_LEARNING_ENV


def resolve_job_names(
    *,
    jobs: Optional[list[str]] = None,
    suite: Optional[str] = None,
) -> list[str]:
    if suite:
        key = suite.strip().lower()
        if key not in JOB_SUITES:
            raise ValueError(f"unknown suite {suite!r}; choose from {sorted(JOB_SUITES)}")
        return list(JOB_SUITES[key])
    if jobs:
        resolved: list[str] = []
        for j in jobs:
            canonical = _JOB_ALIASES.get(j, j)
            if canonical not in ALL_JOBS:
                raise ValueError(f"unknown job(s): {[j]}")
            resolved.append(canonical)
        return resolved
    return list(JOB_SUITES["nightly"])


def _updated_by(triggered_by: str, job_name: str) -> str:
    return f"{triggered_by}:{job_name}"


def _execute_job(
    conn,
    *,
    job_name: str,
    env: str,
    triggered_by: str,
    limit: int,
    lookback_days: int,
    max_results: int,
    min_pricing_samples: int,
    dry_run: bool,
) -> dict[str, Any]:
    if job_name == JOB_RECONCILE_SENT:
        if dry_run:
            return {"dry_run": True, "note": "would call Gmail reconcile"}
        return run_reconcile_all_mailboxes(
            env=env, lookback_days=lookback_days, max_results=max_results,
        )

    if job_name == JOB_BACKFILL_EDIT_LEARNING:
        if dry_run:
            return {
                "dry_run": True,
                "note": "would backfill draft_edit_learning for sent drafts",
                "candidates": len(
                    cal.list_sent_reply_drafts_for_edit_learning(env=env),
                ),
            }
        return backfill_edit_learning_all_mailboxes(env=env, limit=limit)

    if job_name == JOB_APPLY_REJECT_POLICY:
        events = learning_store.list_learning_events(
            conn, env=env, event_types=("draft_rejected_learning",), limit=limit,
        )
        if dry_run:
            return {
                "dry_run": True,
                "events": len(events),
                "preview_chars": len(learning_distill.aggregate_reject_markdown(events)),
            }
        if not events:
            return {"skipped": True, "reason": "no reject events", "events": 0}
        return learning_distill.apply_reject_policy(
            conn,
            env=env,
            updated_by=_updated_by(triggered_by, job_name),
            limit=limit,
        )

    if job_name == JOB_APPLY_EDIT_POLICY:
        events = learning_store.list_learning_events(
            conn, env=env, event_types=("draft_edit_learning",), limit=limit,
        )
        consumed = learning_distill.list_consumed_edit_event_ids(conn, env=env)
        fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
        edited = [e for e in fresh if (e.get("payload") or {}).get("was_edited")]
        threshold = learning_store.style_learning_batch_size()
        if dry_run:
            return {
                "dry_run": True,
                "events": len(events),
                "edited_events": len(edited),
                "batch_threshold": threshold,
                "ready_for_distill": len(edited) >= threshold,
            }
        if not edited:
            return {"skipped": True, "reason": "no edited sent bodies", "events": len(events)}
        return learning_distill.propose_style_learning_approval(
            conn,
            env=env,
            scope="company_style",
            updated_by=_updated_by(triggered_by, job_name),
            limit=limit,
        )

    if job_name == JOB_APPLY_PRICING_POLICY:
        records = learning_store.list_negotiation_history(conn, env=env, limit=limit)
        report = learning_distill.build_pricing_report(records)
        if dry_run:
            return {"dry_run": True, **report}
        if report["sample_size"] == 0:
            return {"skipped": True, "reason": "no negotiation samples", **report}
        return learning_distill.apply_pricing_calibration_policy(
            conn,
            env=env,
            updated_by=_updated_by(triggered_by, job_name),
            limit=limit,
        )

    if job_name == JOB_AUTO_PRICING_CAMPAIGNS:
        campaigns = cal.list_campaigns(env=env)
        promoted: list[dict[str, Any]] = []
        for row in campaigns:
            cid = str(row.get("campaign_id") or "")
            if not cid:
                continue
            ratio = learning_distill.suggest_campaign_paid_ratio(
                conn, env=env, campaign_id=cid, min_samples=min_pricing_samples,
            )
            if ratio is None:
                continue
            if dry_run:
                promoted.append({"campaign_id": cid, "paid_ratio_override": ratio, "dry_run": True})
                continue
            cal.upsert_campaign_config(
                campaign_id=cid, env=env, paid_ratio_override=ratio,
            )
            promoted.append({"campaign_id": cid, "paid_ratio_override": ratio})
        if not promoted:
            return {
                "skipped": True,
                "reason": "no LIVE campaigns met sample threshold",
                "min_pricing_samples": min_pricing_samples,
            }
        return {"promoted_count": len(promoted), "campaigns": promoted}

    if job_name == JOB_APPLY_EDIT_USER_STYLE:
        owner_raw = os.environ.get("KOL_LEARNING_USER_STYLE_OWNER_ID", "").strip()
        # Per-operator calibration: when no fixed owner env is set, derive the
        # operators with unconsumed edits (operator_user_id attribution) and
        # propose a user_style batch for each. Falls back to the single env
        # owner for back-compat / pre-attribution data.
        if owner_raw:
            owner_ids = [int(owner_raw)]
        else:
            owner_ids = learning_distill.list_edit_operator_ids(conn, env=env, limit=limit)
        threshold = learning_store.style_learning_batch_size()
        if dry_run:
            return {
                "dry_run": True,
                "owner_user_ids": owner_ids,
                "batch_threshold": threshold,
                "source": "env_owner" if owner_raw else "operator_attribution",
            }
        if not owner_ids:
            return {
                "skipped": True,
                "reason": (
                    "no KOL_LEARNING_USER_STYLE_OWNER_ID and no operator-attributed "
                    "edits found"
                ),
            }
        results: list[dict[str, Any]] = []
        for oid in owner_ids:
            results.append({
                "owner_user_id": oid,
                **learning_distill.propose_style_learning_approval(
                    conn,
                    env=env,
                    scope="user_style",
                    updated_by=_updated_by(triggered_by, job_name),
                    owner_user_id=oid,
                    limit=limit,
                ),
            })
        proposed = [r for r in results if r.get("pending")]
        return {
            "owner_count": len(owner_ids),
            "proposed_count": len(proposed),
            "results": results,
        }

    if job_name == JOB_ANALYZE_COLLAB_OUTCOME:
        archived = learning_outcome.list_archived_collabs(conn, env=env, limit=limit)
        pending = [
            r for r in archived
            if not learning_outcome.has_outcome_learning_event(
                conn, env=env, identity_id=int(r["identity_id"]),
                campaign_id=r.get("campaign_id"),
            )
        ]
        if dry_run:
            return {
                "dry_run": True,
                "archived_seen": len(archived),
                "pending_retros": len(pending),
            }
        if not pending:
            return {"skipped": True, "reason": "no archived collabs pending retro"}
        return learning_outcome.analyze_pending_collab_outcomes(
            conn, env=env, limit=limit, updated_by=_updated_by(triggered_by, job_name),
        )

    if job_name == JOB_APPLY_OUTCOME_POLICY:
        events = learning_outcome.list_outcome_retro_events(conn, env=env, limit=limit)
        consumed = learning_outcome.list_consumed_outcome_event_ids(conn, env=env)
        fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
        met, gate = learning_outcome._outcome_threshold_met(fresh)
        if dry_run:
            return {
                "dry_run": True,
                "fresh_retros": len(fresh),
                "ready_for_synthesis": met,
                **gate,
            }
        if not fresh:
            return {"skipped": True, "reason": "no new outcome retros"}
        return learning_outcome.propose_outcome_learning_approval(
            conn, env=env, updated_by=_updated_by(triggered_by, job_name), limit=limit,
        )

    if job_name == JOB_APPLY_DISCOVERY_POLICY:
        threshold = learning_discovery.discovery_learning_batch_size()
        fresh = learning_discovery._fresh_decision_events(conn, env=env, limit=limit)
        if dry_run:
            groups = learning_discovery._group_events(conn, fresh)
            return {
                "dry_run": True,
                "fresh_decisions": len(fresh),
                "groups": {
                    f"{kind}:{key}": len(evts)
                    for (kind, key), evts in sorted(groups.items())
                },
                "batch_threshold": threshold,
            }
        if not fresh:
            return {"skipped": True, "reason": "no new shortlist decision events"}
        # Category inference is best-effort prework so the category grouping
        # has data; its failure must not block SPU-level distill.
        category_inference: dict[str, Any]
        try:
            category_inference = learning_discovery.infer_missing_product_categories(
                conn, env=env, updated_by=_updated_by(triggered_by, job_name), limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("category inference failed; continuing distill: %s", exc)
            category_inference = {"error": str(exc)}
        result = learning_discovery.propose_discovery_learning_approval(
            conn,
            env=env,
            updated_by=_updated_by(triggered_by, job_name),
            limit=limit,
        )
        return {**result, "category_inference": category_inference}

    if job_name == JOB_MINE_DISCOVERY_TAGS:
        if dry_run:
            events = learning_store.list_learning_events(
                conn,
                env=env,
                event_types=(discovery_dl.SHORTLIST_DECISION_EVENT,),
                limit=limit,
            )
            with_comment = sum(
                1 for e in events if str((e.get("payload") or {}).get("comment") or "").strip()
            )
            return {
                "dry_run": True,
                "decision_events": len(events),
                "with_comment": with_comment,
                "min_count": learning_discovery.tag_mine_min_count(),
            }
        return learning_discovery.mine_discovery_tags(conn, env=env, limit=limit)

    if job_name == JOB_SYNC_FAILURE_EXAMPLES:
        if dry_run:
            rows = learning_store.list_fact_corrections(conn, env=env, limit=limit)
            return {"dry_run": True, "corrections_count": len(rows)}
        return learning_distill.sync_failure_examples_md(conn, env=env, limit=limit)

    if job_name == JOB_CLASSIFIER_EVAL:
        from . import classifier_eval_runner as cer

        report = cer.run_deterministic_eval()
        if dry_run:
            return {"dry_run": True, **report}
        if report["total"] == 0:
            return {"skipped": True, "reason": "no classifier cases found", **report}
        if report["failed"] > 0:
            return {
                "skipped": False,
                "eval_failed": True,
                **report,
            }
        return report

    if job_name == JOB_SNAPSHOT_FACT_CORRECTIONS:
        rows = learning_store.list_fact_corrections(conn, env=env, limit=limit)
        sample = [
            {
                "identity_id": r.get("identity_id"),
                "campaign_id": r.get("campaign_id"),
                "fact_key": r.get("fact_key"),
                "manual_at": r.get("manual_at"),
            }
            for r in rows[:10]
        ]
        if dry_run:
            return {"dry_run": True, "corrections_count": len(rows), "sample": sample}
        return {"corrections_count": len(rows), "sample": sample}

    raise ValueError(f"unknown job: {job_name}")


def run_single_job(
    conn,
    *,
    job_name: str,
    env: str,
    triggered_by: str,
    limit: int = 200,
    lookback_days: int = 7,
    max_results: int = 100,
    min_pricing_samples: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one job with full audit row (always recorded unless dry_run)."""
    input_payload = {
        "limit": limit,
        "lookback_days": lookback_days,
        "max_results": max_results,
        "min_pricing_samples": min_pricing_samples,
        "dry_run": dry_run,
    }
    if dry_run:
        try:
            output = _execute_job(
                conn,
                job_name=job_name,
                env=env,
                triggered_by=triggered_by,
                limit=limit,
                lookback_days=lookback_days,
                max_results=max_results,
                min_pricing_samples=min_pricing_samples,
                dry_run=True,
            )
            return {
                "job_name": job_name,
                "env": env,
                "status": job_store.JOB_STATUS_OK,
                "dry_run": True,
                "output": output,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "job_name": job_name,
                "env": env,
                "status": job_store.JOB_STATUS_ERROR,
                "dry_run": True,
                "error": str(exc),
            }

    run_id = job_store.start_run(
        conn,
        job_name=job_name,
        env=env,
        triggered_by=triggered_by,
        input_payload=input_payload,
    )
    row = conn.execute(
        "SELECT started_at FROM kol_learning_job_runs WHERE id=?", (run_id,),
    ).fetchone()
    started_at = row["started_at"] if row else None
    try:
        output = _execute_job(
            conn,
            job_name=job_name,
            env=env,
            triggered_by=triggered_by,
            limit=limit,
            lookback_days=lookback_days,
            max_results=max_results,
            min_pricing_samples=min_pricing_samples,
            dry_run=False,
        )
        if output.get("eval_failed"):
            status = job_store.JOB_STATUS_ERROR
        elif output.get("skipped") is True:
            status = job_store.JOB_STATUS_SKIPPED
        else:
            status = job_store.JOB_STATUS_OK
        finished = job_store.finish_run(
            conn, run_id, status=status, output=output, started_at=started_at,
        )
        return {"run_id": run_id, **finished}
    except GmailUnavailable as exc:
        finished = job_store.finish_run(
            conn,
            run_id,
            status=job_store.JOB_STATUS_SKIPPED,
            output={"skipped": True, "reason": "gmail_unavailable"},
            error_message=str(exc),
            started_at=started_at,
        )
        return {"run_id": run_id, **finished}
    except Exception as exc:  # noqa: BLE001
        log.exception("learning job %s failed", job_name)
        finished = job_store.finish_run(
            conn,
            run_id,
            status=job_store.JOB_STATUS_ERROR,
            output={},
            error_message=str(exc),
            started_at=started_at,
        )
        return {"run_id": run_id, **finished}


def run_scheduled_jobs(
    *,
    env: str,
    triggered_by: str = "cron:learning",
    jobs: Optional[list[str]] = None,
    suite: Optional[str] = None,
    limit: int = 200,
    lookback_days: int = 7,
    max_results: int = 100,
    min_pricing_samples: Optional[int] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a batch of learning jobs for LIVE only; returns per-job results."""
    env = require_scheduled_learning_env(env)
    if os.environ.get("KOL_LEARNING_JOBS_DISABLED", "").strip().lower() in {
        "1", "true", "yes",
    }:
        return {
            "ok": True,
            "env": env,
            "disabled": True,
            "results": [],
        }

    # Skip-if-running guard: never let a second learning run pile onto an
    # in-flight one (capture stacking → bridge starvation). Dry-runs are
    # cheap previews and bypass the lock.
    if not dry_run:
        acquired = _RUN_LOCK.acquire(blocking=False)
        if not acquired:
            _debug_log("learning_run_skipped_locked", {
                "suite": suite, "triggered_by": triggered_by, "env": env,
            })
            return {
                "ok": True,
                "env": env,
                "triggered_by": triggered_by,
                "suite": suite,
                "skipped": True,
                "reason": "learning_run_in_progress",
                "results": [],
            }
        try:
            _debug_log("learning_run_started", {
                "suite": suite, "triggered_by": triggered_by, "env": env,
            })
            return _run_scheduled_jobs_locked(
                env=env,
                triggered_by=triggered_by,
                jobs=jobs,
                suite=suite,
                limit=limit,
                lookback_days=lookback_days,
                max_results=max_results,
                min_pricing_samples=min_pricing_samples,
                dry_run=dry_run,
            )
        finally:
            _RUN_LOCK.release()

    return _run_scheduled_jobs_locked(
        env=env,
        triggered_by=triggered_by,
        jobs=jobs,
        suite=suite,
        limit=limit,
        lookback_days=lookback_days,
        max_results=max_results,
        min_pricing_samples=min_pricing_samples,
        dry_run=dry_run,
    )


def _run_scheduled_jobs_locked(
    *,
    env: str,
    triggered_by: str,
    jobs: Optional[list[str]],
    suite: Optional[str],
    limit: int,
    lookback_days: int,
    max_results: int,
    min_pricing_samples: Optional[int],
    dry_run: bool,
) -> dict[str, Any]:
    """Inner body of :func:`run_scheduled_jobs` (runs under the run lock)."""
    min_samples = min_pricing_samples
    if min_samples is None:
        min_samples = int(os.environ.get("KOL_LEARNING_MIN_PRICING_SAMPLES", "3"))

    names = resolve_job_names(jobs=jobs, suite=suite)
    if (
        os.environ.get("KOL_LEARNING_USER_STYLE_OWNER_ID", "").strip()
        and JOB_APPLY_EDIT_USER_STYLE not in names
        and suite in (None, "nightly", "all")
    ):
        names = list(names) + [JOB_APPLY_EDIT_USER_STYLE]
    results: list[dict[str, Any]] = []
    with cal._connect() as conn:  # type: ignore[attr-defined]
        for name in names:
            results.append(run_single_job(
                conn,
                job_name=name,
                env=env,
                triggered_by=triggered_by,
                limit=limit,
                lookback_days=lookback_days,
                max_results=max_results,
                min_pricing_samples=min_samples,
                dry_run=dry_run,
            ))
    ok = sum(
        1 for r in results
        if r.get("status") in {job_store.JOB_STATUS_OK, "ok", None}
        and not r.get("dry_run")
    )
    skipped = sum(1 for r in results if r.get("status") == job_store.JOB_STATUS_SKIPPED)
    errors = sum(1 for r in results if r.get("status") == job_store.JOB_STATUS_ERROR)
    return {
        "ok": errors == 0,
        "env": env,
        "triggered_by": triggered_by,
        "suite": suite,
        "jobs": names,
        "summary": {"ok": ok, "skipped": skipped, "errors": errors},
        "results": results,
    }
