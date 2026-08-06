"""Autopilot mode — deterministic timed auto-send of low-risk drafts (PR2).

Default OFF (``autopilot_enabled`` setting = false). When enabled, each
``draft_ready`` handoff schedules a ``cs_autopilot_jobs`` row with a countdown
(``send_at`` = now + ``autopilot_send_after_sec``). A background worker claims
due jobs and sends the CAL-stored draft via the same service path the Console
uses (``send_reply.send_reply``). If the operator edits the draft before the
timer fires, the baseline-hash mismatch cancels the job (the edit unlocks the
draft). Operators can also POST /autopilot/cancel to abort.

Lock: while a job is ``scheduled`` the draft is locked — PUT /draft refuses
with 409 (enforced via ``autopilot_lock_check`` wired into cal.save_draft).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from . import cal

log = logging.getLogger(__name__)

SETTING_ENABLED = "autopilot_enabled"
SETTING_SEND_AFTER_SEC = "autopilot_send_after_sec"
DEFAULT_SEND_AFTER_SEC = 180


def is_enabled() -> bool:
    return bool(cal.get_setting(SETTING_ENABLED, default=False))


def send_after_sec() -> int:
    val = cal.get_setting(SETTING_SEND_AFTER_SEC, default=DEFAULT_SEND_AFTER_SEC)
    try:
        n = int(val)
        return n if n > 0 else DEFAULT_SEND_AFTER_SEC
    except (TypeError, ValueError):
        return DEFAULT_SEND_AFTER_SEC


def get_settings() -> dict[str, Any]:
    return {
        SETTING_ENABLED: is_enabled(),
        SETTING_SEND_AFTER_SEC: send_after_sec(),
    }


def update_settings(*, enabled: Optional[bool] = None, send_after_sec: Optional[int] = None, updated_by: str = "console") -> dict[str, Any]:
    if enabled is not None:
        cal.set_setting(SETTING_ENABLED, bool(enabled), updated_by=updated_by)
    if send_after_sec is not None:
        cal.set_setting(SETTING_SEND_AFTER_SEC, int(send_after_sec), updated_by=updated_by)
    return get_settings()


def draft_baseline_hash(draft_html: str) -> str:
    return hashlib.sha256((draft_html or "").encode("utf-8")).hexdigest()[:16]


def on_draft_ready(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    """``draft_ready`` hook — schedule an autopilot job if enabled.

    Returns the created job row, or None when autopilot is off / a job already
    exists / the session has no draft.
    """
    if not is_enabled():
        return None
    sess = cal.get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess or not sess.get("draft_html"):
        return None
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    send_at = (now + timedelta(seconds=send_after_sec())).isoformat()
    job = cal.create_autopilot_job(
        quickcep_session_id=quickcep_session_id,
        env=env,
        send_at=send_at,
        baseline_hash=draft_baseline_hash(sess["draft_html"]),
    )
    if job:
        cal.write_event(
            quickcep_session_id=quickcep_session_id,
            env=env,
            event_type="autopilot_scheduled",
            payload={"job_id": job["id"], "send_at": send_at},
        )
        log.info(
            "cs.autopilot.schedule session=%s env=%s job_id=%s send_at=%s "
            "baseline_hash=%s decision=scheduled",
            quickcep_session_id, env, job["id"], send_at, job.get("baseline_hash") or "",
        )
    return job


def autopilot_lock_check(session: dict[str, Any]) -> Optional[str]:
    """Return a lock reason if the session draft is locked by a scheduled job, else None."""
    job = cal.get_latest_autopilot_job(
        quickcep_session_id=session.get("quickcep_session_id", ""),
        env=session.get("env", "LIVE"),
    )
    if job and job["status"] == "scheduled":
        return (
            "autopilot countdown running — POST /autopilot/cancel to edit the draft"
        )
    return None


def run_autopilot_tick(*, env: str = "LIVE") -> dict[str, Any]:
    """Claim due scheduled jobs and send their drafts.

    Baseline-hash guard: if the stored draft no longer matches the baseline
    (operator edited via the legacy path or hash drifted), the job is cancelled
    rather than sent. Designed to be called on a 15-30s interval.
    """
    from datetime import datetime, timezone

    if not is_enabled():
        return {"ok": True, "enabled": False, "sent": 0, "cancelled": 0, "failed": 0}
    now_iso = datetime.now(timezone.utc).isoformat()
    claimed = cal.claim_scheduled_autopilot_jobs(now_iso=now_iso)
    sent = cancelled = failed = 0
    for job in claimed:
        qsid = job["quickcep_session_id"]
        current_hash = draft_baseline_hash(job.get("draft_html") or "")
        if current_hash != job["baseline_hash"]:
            # Draft changed since scheduling — operator edited; abort send.
            cal.finalize_autopilot_job(job_id=job["id"], status="cancelled")
            cal.write_event(
                quickcep_session_id=qsid, env=env,
                event_type="autopilot_cancelled",
                payload={"reason": "baseline_hash_mismatch", "job_id": job["id"]},
            )
            cancelled += 1
            log.info(
                "cs.autopilot.send session=%s env=%s job_id=%s decision=cancelled "
                "reason=baseline_hash_mismatch", qsid, env, job["id"],
            )
            continue
        try:
            from .send_reply import send_reply

            result = send_reply(quickcep_session_id=qsid, env=env, operator_id="autopilot", operator_name="Autopilot")
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("autopilot send failed session=%s: %s", qsid, exc)
            cal.finalize_autopilot_job(job_id=job["id"], status="failed")
            failed += 1
            log.info(
                "cs.autopilot.send session=%s env=%s job_id=%s decision=failed "
                "reason=exception error=%s", qsid, env, job["id"], str(exc)[:200],
            )
            continue
        if result.get("ok"):
            cal.finalize_autopilot_job(job_id=job["id"], status="sent", message_id=str(result.get("message_id") or ""))
            sent += 1
            log.info(
                "cs.autopilot.send session=%s env=%s job_id=%s decision=sent "
                "message_id=%s", qsid, env, job["id"], result.get("message_id") or "",
            )
        else:
            cal.finalize_autopilot_job(job_id=job["id"], status="failed")
            failed += 1
            log.info(
                "cs.autopilot.send session=%s env=%s job_id=%s decision=failed "
                "reason=send_failed error=%s",
                qsid, env, job["id"], result.get("error") or "",
            )
    return {"ok": True, "enabled": True, "sent": sent, "cancelled": cancelled,
            "failed": failed, "claimed": len(claimed)}


def get_session_autopilot(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    job = cal.get_latest_autopilot_job(quickcep_session_id=quickcep_session_id, env=env)
    if not job:
        return None
    return {
        "job_id": job["id"],
        "status": job["status"],
        "send_at": job["send_at"],
        "baseline_hash": job["baseline_hash"],
        "claimed_at": job.get("claimed_at"),
    }


def cancel_session_autopilot(*, quickcep_session_id: str, env: str = "LIVE", reason: str = "operator_cancelled") -> dict[str, Any]:
    return cal.cancel_autopilot_job(quickcep_session_id=quickcep_session_id, env=env, reason=reason)
