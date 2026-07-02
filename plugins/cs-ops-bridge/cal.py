"""SQLite CAL for cs-ops-bridge."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from .schema import ESCALATION_STATES, SESSION_STATUSES, recreate_all
from .pii_sanitize import mask_string, sanitize_mapping, sanitize_namespaces

log = logging.getLogger(__name__)

_DB_PATH = Path(
    os.environ.get(
        "HERMES_CS_OPS_CAL_DB",
        Path(os.path.expanduser("~/.hermes/cs-ops-bridge/cal.db")),
    )
)
_DEBUG_LOG_PATH = Path("/Users/arnold/agent_prj/.cursor/debug-922c3e.log")
_DEBUG_SESSION_LOG_PATH = Path("/Users/arnold/.cursor/debug-logs/debug-eb3761.log")
_TERMINAL_DRAFT_STATUSES = frozenset({"operator_replied", "reviewed", "skipped"})


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "922c3e",
            "id": f"log_{int(time.time() * 1000)}_{hypothesis_id}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _debug_session_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "eb3761",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
        }
        with _DEBUG_SESSION_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _is_effectively_empty_draft(html: str) -> bool:
    import re

    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.replace("\xa0", " ").strip()
    return not text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    recreate_all(conn)
    return conn


def health() -> dict[str, Any]:
    with _connect() as conn:
        sessions = conn.execute("SELECT COUNT(*) AS n FROM cs_session").fetchone()["n"]
        open_esc = conn.execute(
            "SELECT COUNT(*) AS n FROM cs_escalations WHERE state='awaiting_answer'"
        ).fetchone()["n"]
    return {"ok": True, "db": str(_DB_PATH), "sessions": sessions, "open_escalations": open_esc}


def perf_snapshot(*, env: str = "LIVE") -> dict[str, Any]:
    """Operator-facing bridge metrics (poller lag, queue-ish counts)."""
    with _connect() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM cs_session WHERE env=? GROUP BY status",
            (env,),
        ).fetchall()
        open_esc = conn.execute(
            "SELECT COUNT(*) AS n FROM cs_escalations WHERE state='awaiting_answer' AND env=?",
            (env,),
        ).fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM cs_session WHERE env=? AND status='pending'",
            (env,),
        ).fetchone()["n"]
    pollers = {
        name: get_poller_state(name)
        for name in ("quickcep_watcher", "feishu_escalation_poller", "escalation_timeout")
    }
    return {
        "env": env,
        "sessions_by_status": {str(r["status"]): r["n"] for r in by_status},
        "pending_sessions": pending,
        "open_escalations": open_esc,
        "pollers": pollers,
    }


def enqueue_session(
    *,
    quickcep_session_id: str,
    chat_session_id: Optional[str] = None,
    customer_email: Optional[str] = None,
    message_id: str,
    env: str = "LIVE",
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    locale: Optional[str] = None,
    email_subject: Optional[str] = None,
    last_message_preview: Optional[str] = None,
    intention_tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Idempotent enqueue; returns ``created`` flag and session row.

    Optional visitor/draft-preview fields (PR1.2) are persisted with COALESCE
    so a re-enqueue for a follow-up message never wipes previously stored values.
    """
    dedup_key = f"{env}:{quickcep_session_id}:{message_id}"
    now = _now()
    tags_json = (
        json.dumps([str(t) for t in intention_tags if str(t).strip()], ensure_ascii=False)
        if intention_tags
        else None
    )
    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM cs_message_dedup WHERE dedup_key=?",
            (dedup_key,),
        ).fetchone()
        if existing:
            row = conn.execute(
                "SELECT * FROM cs_session WHERE quickcep_session_id=? AND env=?",
                (quickcep_session_id, env),
            ).fetchone()
            return {"created": False, "deduped": True, "should_launch": False, "session": dict(row) if row else None}

        conn.execute(
            "INSERT INTO cs_message_dedup(dedup_key, quickcep_session_id, message_id, env, created_at)"
            " VALUES (?,?,?,?,?)",
            (dedup_key, quickcep_session_id, message_id, env, now),
        )
        conn.execute(
            """INSERT INTO cs_session(
                   quickcep_session_id, chat_session_id, customer_email,
                   last_message_id, status, env, created_at, updated_at,
                   customer_name, customer_company, locale,
                   email_subject, last_message_preview, intention_tags
               ) VALUES (?,?,?,?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(quickcep_session_id, env) DO UPDATE SET
                   chat_session_id=COALESCE(excluded.chat_session_id, chat_session_id),
                   customer_email=COALESCE(excluded.customer_email, customer_email),
                   last_message_id=excluded.last_message_id,
                   status=CASE WHEN status IN ('draft_ready','operator_replied','skipped','failed','reviewed') THEN 'pending'
                            ELSE status END,
                   updated_at=excluded.updated_at,
                   customer_name=COALESCE(excluded.customer_name, customer_name),
                   customer_company=COALESCE(excluded.customer_company, customer_company),
                   locale=COALESCE(excluded.locale, locale),
                   email_subject=COALESCE(excluded.email_subject, email_subject),
                   last_message_preview=COALESCE(excluded.last_message_preview, last_message_preview),
                   intention_tags=COALESCE(excluded.intention_tags, intention_tags)
            """,
            (
                quickcep_session_id, chat_session_id, customer_email, message_id, env, now, now,
                customer_name, customer_company, locale, email_subject, last_message_preview, tags_json,
            ),
        )
        row = conn.execute(
            "SELECT * FROM cs_session WHERE quickcep_session_id=? AND env=?",
            (quickcep_session_id, env),
        ).fetchone()
        session = dict(row)
        status = session["status"]
        should_launch = status == "pending"
        event_type = "inbound_received"
        if not should_launch:
            event_type = "customer_followup_while_busy"
        conn.execute(
            """INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at)
               VALUES (?,?,?,?,?)""",
            (
                session["id"],
                event_type,
                json.dumps({"message_id": message_id, "status": status}),
                env,
                now,
            ),
        )
        conn.commit()
        result = {
            "created": True,
            "deduped": False,
            "should_launch": should_launch,
            "session": session,
        }
        return result


# Columns updatable by enrich_session (PR1.2). Each maps field -> column name.
_ENRICHABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("customer_email", "customer_email"),
    ("customer_name", "customer_name"),
    ("customer_company", "customer_company"),
    ("locale", "locale"),
    ("email_subject", "email_subject"),
    ("last_message_preview", "last_message_preview"),
    ("intention_tags", "intention_tags"),
    ("draft_html", "draft_html"),
    ("draft_attachments", "draft_attachments"),
    ("draft_source", "draft_source"),
)


def enrich_session(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
    intention_tags: Optional[list[str]] = None,
    **fields: Any,
) -> bool:
    """Update visitor/draft-preview columns on an existing session row.

    Only non-None values are written (COALESCE-style: never overwrite an
    existing value with NULL). ``intention_tags`` is accepted as a list and
    serialized to JSON. Returns True if the session was found.
    """
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return False
    if intention_tags is not None:
        fields["intention_tags"] = json.dumps(
            [str(t) for t in intention_tags if str(t).strip()], ensure_ascii=False
        )
    sets: list[str] = []
    params: list[Any] = []
    for field, col in _ENRICHABLE_COLUMNS:
        val = fields.get(field)
        if val is None:
            continue
        # COALESCE keeps the existing column value when the new one is NULL-ish;
        # since we skip None above, this mainly guards against empty strings.
        sets.append(f"{col}=COALESCE(NULLIF(?, ''), {col})")
        params.append(val)
    if not sets:
        return True
    params.extend([_now(), quickcep_session_id, env])
    with _connect() as conn:
        conn.execute(
            f"UPDATE cs_session SET {', '.join(sets)}, updated_at=? "
            f"WHERE quickcep_session_id=? AND env=?",
            params,
        )
        conn.commit()
    return True


def list_sessions(
    *,
    env: str = "LIVE",
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        sql = """SELECT * FROM cs_session WHERE env=?"""
        params: list[Any] = [env]
        if status:
            sql += " AND status=?"
            params.append(status)
        if q:
            like = f"%{q.strip()}%"
            sql += " AND (quickcep_session_id LIKE ? OR customer_email LIKE ? OR chat_session_id LIKE ?)"
            params.extend([like, like, like])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_session(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM cs_session WHERE quickcep_session_id=? AND env=?",
            (quickcep_session_id, env),
        ).fetchone()
        return dict(row) if row else None


def _parse_json_col(row: dict[str, Any], col: str, default: Any) -> Any:
    raw = row.get(col)
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def session_counts(*, env: str = "LIVE") -> dict[str, int]:
    """Count of sessions per status + total, for the sessions list header (PR1.4)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM cs_session WHERE env=? GROUP BY status",
            (env,),
        ).fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def _latest_autopilot_job(conn: sqlite3.Connection, session_id: int, env: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM cs_autopilot_jobs WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 1",
        (session_id, env),
    ).fetchone()
    return dict(row) if row else None


def get_workbench(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    """L1 pure-CAL aggregate for the Console workbench (PR1.4).

    Zero QuickCEP calls. Combines: session row + parsed draft/attachments +
    intention_tags + classify fact + latest escalation + recent events +
    latest autopilot job.
    """
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    sid = sess["id"]
    with _connect() as conn:
        facts_rows = conn.execute(
            "SELECT namespace, fact_key, fact_value_json FROM cs_facts WHERE session_id=? AND env=?",
            (sid, env),
        ).fetchall()
        events = conn.execute(
            """SELECT event_type, payload_json, created_at FROM cs_conversation_events
               WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 30""",
            (sid, env),
        ).fetchall()
        esc = conn.execute(
            "SELECT * FROM cs_escalations WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 1",
            (sid, env),
        ).fetchone()
        esc_rows = conn.execute(
            "SELECT * FROM cs_escalations WHERE session_id=? AND env=? ORDER BY id ASC",
            (sid, env),
        ).fetchall()
        ap_job = _latest_autopilot_job(conn, sid, env)
    facts_map: dict[str, dict[str, Any]] = {}
    for f in facts_rows:
        facts_map.setdefault(f["namespace"], {})[f["fact_key"]] = json.loads(f["fact_value_json"])

    session_out = dict(sess)
    session_out["intention_tags"] = _parse_json_col(sess, "intention_tags", [])
    session_out["draft_attachments"] = _parse_json_col(sess, "draft_attachments", [])

    return {
        "session": session_out,
        "draft": {
            "html": sess.get("draft_html"),
            "attachments": _parse_json_col(sess, "draft_attachments", []),
            "source": sess.get("draft_source"),
            "updated_at": sess.get("draft_updated_at"),
        },
        "classify": facts_map.get("classify", {}),
        "intention_tags": _parse_json_col(sess, "intention_tags", []),
        "latest_escalation": (dict(esc) if esc else None),
        "escalations": [dict(r) for r in esc_rows],
        "autopilot_job": (dict(ap_job) if ap_job else None),
        "recent_events": [
            {"event_type": e["event_type"], "payload": json.loads(e["payload_json"] or "{}"),
             "created_at": e["created_at"]}
            for e in events
        ],
    }


def get_session_state(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    """L3 lightweight poll payload for the Console (PR1.4).

    Only the fields the FE polls frequently to detect changes: status, latest
    escalation state, autopilot send_at, last_message_id, draft source/updated.
    """
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    sid = sess["id"]
    with _connect() as conn:
        esc = conn.execute(
            "SELECT state FROM cs_escalations WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 1",
            (sid, env),
        ).fetchone()
        ap_job = _latest_autopilot_job(conn, sid, env)
    return {
        "status": sess["status"],
        "last_message_id": sess.get("last_message_id"),
        "updated_at": sess.get("updated_at"),
        "draft_source": sess.get("draft_source"),
        "draft_updated_at": sess.get("draft_updated_at"),
        "escalation_state": esc["state"] if esc else None,
        "autopilot": {
            "status": ap_job["status"] if ap_job else None,
            "send_at": ap_job["send_at"] if ap_job else None,
        } if ap_job else None,
    }


# ── Autopilot settings + job CRUD (PR2) ────────────────────────────────


def get_setting(key: str, *, default: Any = None) -> Any:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value_json FROM cs_settings WHERE key=?", (key,),
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value: Any, *, updated_by: str = "system") -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cs_settings(key, value_json, updated_at, updated_by)
               VALUES(?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   value_json=excluded.value_json,
                   updated_at=excluded.updated_at,
                   updated_by=excluded.updated_by""",
            (key, json.dumps(value, ensure_ascii=False), _now(), updated_by),
        )
        conn.commit()


def get_all_settings() -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value_json FROM cs_settings").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value_json"])
        except (json.JSONDecodeError, TypeError):
            out[r["key"]] = None
    return out


def create_autopilot_job(
    *,
    quickcep_session_id: str,
    env: str,
    send_at: str,
    baseline_hash: str,
) -> Optional[dict[str, Any]]:
    """Schedule an autopilot send job. Returns the job row or None on conflict."""
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    now = _now()
    with _connect() as conn:
        try:
            conn.execute(
                """INSERT INTO cs_autopilot_jobs(
                       session_id, env, baseline_hash, send_at, status, created_at, updated_at
                   ) VALUES(?,?,?,?, 'scheduled', ?, ?)""",
                (sess["id"], env, baseline_hash, send_at, now, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # UNIQUE(session_id, env) — a job already exists for this session.
            return None
    return get_latest_autopilot_job(quickcep_session_id=quickcep_session_id, env=env)


def get_latest_autopilot_job(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM cs_autopilot_jobs WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 1",
            (sess["id"], env),
        ).fetchone()
    return dict(row) if row else None


def claim_scheduled_autopilot_jobs(*, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
    """Atomically claim due scheduled jobs (scheduled → sending). Returns claimed rows."""
    claimed: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = conn.execute(
            """SELECT j.*, s.quickcep_session_id, s.draft_html, s.draft_source
               FROM cs_autopilot_jobs j
               INNER JOIN cs_session s ON s.id = j.session_id AND s.env = j.env
               WHERE j.status='scheduled' AND j.send_at <= ?
               ORDER BY j.send_at ASC LIMIT ?""",
            (now_iso, limit),
        ).fetchall()
        for r in rows:
            cur = conn.execute(
                "UPDATE cs_autopilot_jobs SET status='sending', claimed_at=?, updated_at=? "
                "WHERE id=? AND status='scheduled'",
                (_now(), _now(), r["id"]),
            )
            if cur.rowcount == 1:
                claimed.append(dict(r))
        conn.commit()
    return claimed


def finalize_autopilot_job(*, job_id: int, status: str, message_id: str = "") -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE cs_autopilot_jobs SET status=?, updated_at=? WHERE id=?",
            (status, _now(), job_id),
        )
        conn.commit()
        return cur.rowcount == 1


def cancel_autopilot_job(*, quickcep_session_id: str, env: str = "LIVE", reason: str = "operator_cancelled") -> dict[str, Any]:
    """Cancel a scheduled/sending autopilot job for a session (operator override)."""
    job = get_latest_autopilot_job(quickcep_session_id=quickcep_session_id, env=env)
    if not job:
        return {"ok": False, "error": "no_autopilot_job"}
    if job["status"] in ("sent", "cancelled", "failed"):
        return {"ok": False, "skipped": True, "reason": f"job already {job['status']}"}
    finalize_autopilot_job(job_id=job["id"], status="cancelled")
    write_event(
        quickcep_session_id=quickcep_session_id,
        env=env,
        event_type="autopilot_cancelled",
        payload={"reason": reason, "job_id": job["id"]},
    )
    return {"ok": True, "job_id": job["id"], "status": "cancelled"}


def save_draft(
    *,
    quickcep_session_id: str,
    draft_html: str,
    attachments: Optional[list[Any]] = None,
    source: str = "agent",
    subject: Optional[str] = None,
    env: str = "LIVE",
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
    lock_check: Optional[Callable[[dict[str, Any]], Optional[str]]] = None,
) -> dict[str, Any]:
    """Persist a reply draft to CAL (cs_session.draft_html + draft_*).

    Replaces writing drafts to QuickCEP (PR1.3 / §4.13). The agent contract
    (cs_bridge_tool draft-save command) is unchanged — only the storage target
    moves from QuickCEP to CAL, eliminating the ownerId draft-visibility problem.

    ``lock_check`` (used by Autopilot, PR2) may return a lock-reason string; when
    non-None the write is refused with a 409-style ``draft_locked`` result.

    Returns a dict shaped for compatibility with the legacy quickcep_cli
    draft-save response: ``{action, success, stored, ...}``.
    """
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return {"action": "draft_save", "success": False, "error": "session not found"}
    session_status = str(sess.get("status") or "")
    if source == "operator_edit" and (
        session_status in _TERMINAL_DRAFT_STATUSES or sess.get("draft_source") == "sent"
    ):
        if _is_effectively_empty_draft(draft_html):
            _debug_session_log(
                hypothesis_id="H4",
                location="cal.py:save_draft",
                message="skip ghost draft save after send",
                data={
                    "quickcep_session_id": quickcep_session_id,
                    "session_status": session_status,
                    "draft_source": sess.get("draft_source"),
                },
            )
            return {
                "action": "draft_save",
                "success": True,
                "stored": "skipped",
                "reason": "terminal_empty_draft",
                "session_id": quickcep_session_id,
            }
    if (sess.get("draft_html") or "") == draft_html and (sess.get("draft_source") or "") == source:
        _debug_session_log(
            hypothesis_id="H2",
            location="cal.py:save_draft",
            message="skip unchanged draft save",
            data={"quickcep_session_id": quickcep_session_id, "source": source},
        )
        return {
            "action": "draft_save",
            "success": True,
            "stored": "unchanged",
            "session_id": quickcep_session_id,
            "source": source,
        }
    if lock_check is not None:
        reason = lock_check(sess)
        if reason:
            return {
                "action": "draft_save",
                "success": False,
                "error": "draft_locked_autopilot",
                "error_detail": reason,
                "session_id": quickcep_session_id,
            }
    now = _now()
    att_json = json.dumps(attachments or [], ensure_ascii=False) if attachments is not None else None
    # PR3: when an operator edit overwrites the agent draft, snapshot the AI
    # baseline so the edit-memory run can diff the two drafts. Only snapshot
    # when there is an actual prior agent draft and the new content differs.
    if source == "operator_edit" and (sess.get("draft_source") == "agent") and sess.get("draft_html"):
        if (sess.get("draft_html") or "") != draft_html:
            write_facts(
                quickcep_session_id=quickcep_session_id,
                env=env,
                namespaces={"edit_memory": {"ai_baseline_html": sess.get("draft_html")}},
            )
    with _connect() as conn:
        sets = [
            "draft_html=?",
            "draft_attachments=COALESCE(?, draft_attachments)",
            "draft_source=?",
            "draft_updated_at=?",
            "updated_at=?",
        ]
        params: list[Any] = [draft_html, att_json, source, now, now]
        if subject:
            sets.append("email_subject=COALESCE(NULLIF(?, ''), email_subject)")
            params.append(subject)
        params.extend([quickcep_session_id, env])
        conn.execute(
            f"UPDATE cs_session SET {', '.join(sets)} WHERE quickcep_session_id=? AND env=?",
            params,
        )
        conn.execute(
            """INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at)
               VALUES (?,?,?,?,?)""",
            (
                sess["id"],
                "draft_saved",
                json.dumps({
                    "source": source,
                    "attachments": len(attachments or []),
                    "subject": subject,
                    "operator_id": operator_id,
                    "operator_name": operator_name,
                }),
                env,
                now,
            ),
        )
        conn.commit()
    return {
        "action": "draft_save",
        "success": True,
        "stored": "cal",
        "session_id": quickcep_session_id,
        "source": source,
        "attachments": len(attachments or []),
    }


def clear_draft(*, quickcep_session_id: str, env: str = "LIVE") -> None:
    """Clear the CAL draft after a successful send (the reply is no longer pending).

    Snapshots the current draft into sent_draft_html / sent_draft_source so the
    daily report can still compute adoption rates after the composer is cleared.
    Sets draft_html/draft_attachments to empty and draft_source to "sent" so a
    workbench reload does not re-show the just-sent content in the composer.
    """
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return
    now = _now()
    prior_html = sess.get("draft_html") or ""
    prior_source = sess.get("draft_source") or ""
    with _connect() as conn:
        # Snapshot the draft that was actually sent (only if it was AI-generated
        # and not already a "sent" placeholder from a prior clear).
        if prior_html and prior_source in ("agent", "operator_edit", "resume_agent"):
            conn.execute(
                "UPDATE cs_session SET sent_draft_html=?, sent_draft_source=?, sent_draft_at=?, "
                "draft_html='', draft_attachments='[]', draft_source='sent', "
                "draft_updated_at=?, updated_at=? WHERE quickcep_session_id=? AND env=?",
                (prior_html, prior_source, now, now, now, quickcep_session_id, env),
            )
        else:
            conn.execute(
                "UPDATE cs_session SET draft_html='', draft_attachments='[]', draft_source='sent', "
                "draft_updated_at=?, updated_at=? WHERE quickcep_session_id=? AND env=?",
                (now, now, quickcep_session_id, env),
            )
        conn.commit()


def session_has_event(*, session_row_id: int, event_type: str) -> bool:
    """True when at least one CAL conversation event of ``event_type`` exists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM cs_conversation_events WHERE session_id=? AND event_type=? LIMIT 1",
            (session_row_id, event_type),
        ).fetchone()
        return row is not None


def session_has_open_escalation(*, quickcep_session_id: str, env: str = "LIVE") -> bool:
    """True when the session has awaiting_answer or resuming escalation rows."""
    return bool(
        list_escalations_for_session(
            quickcep_session_id=quickcep_session_id,
            states=("awaiting_answer", "resuming"),
            env=env,
        )
    )


def list_sessions_with_open_escalations(*, env: str = "LIVE", limit: int = 200) -> list[dict[str, Any]]:
    """Sessions joined to open escalations (awaiting_answer or resuming)."""
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT s.* FROM cs_session s
               INNER JOIN cs_escalations e ON e.session_id = s.id AND e.env = s.env
               WHERE s.env=? AND e.state IN ('awaiting_answer', 'resuming')
               ORDER BY s.updated_at DESC LIMIT ?""",
            (env, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def has_prior_session_for_email(
    *,
    customer_email: str,
    env: str = "LIVE",
    exclude_quickcep_session_id: Optional[str] = None,
) -> bool:
    """True when CAL already has another session row for this customer email."""
    email = (customer_email or "").strip().lower()
    if not email or "@" not in email:
        return False
    with _connect() as conn:
        sql = "SELECT 1 FROM cs_session WHERE env=? AND lower(customer_email)=?"
        params: list[Any] = [env, email]
        if exclude_quickcep_session_id:
            sql += " AND quickcep_session_id != ?"
            params.append(exclude_quickcep_session_id)
        sql += " LIMIT 1"
        return conn.execute(sql, params).fetchone() is not None


def update_session_chat_id(*, session_row_id: int, chat_session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE cs_session SET chat_session_id=?, updated_at=? WHERE id=?",
            (chat_session_id, _now(), session_row_id),
        )
        conn.commit()


def update_session_status(*, session_row_id: int, status: str) -> None:
    if status not in SESSION_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with _connect() as conn:
        conn.execute(
            "UPDATE cs_session SET status=?, updated_at=? WHERE id=?",
            (status, _now(), session_row_id),
        )
        conn.commit()


def _fetch_visitor_orders(quickcep_session_id: str, env: str = "LIVE") -> dict[str, Any]:
    """Fetch customer orders from QuickCEP getOrderList API.

    Uses the same quickcep_cli import pattern as email_channel.fetch_email_session_row.
    Returns {"orders": [...], "userUUID": str|None, "customer_email": str|None, "source": str}.
    Best-effort: on any failure returns empty orders list.

    As a side effect (PR1.2), enriches the CAL session row with visitorInfo
    name/locale and intentionTags when they are missing — the SIO enqueue path
    does not carry these, so the first dispatch-context fetch backfills them.
    """
    import argparse as _ap
    import sys as _sys

    try:
        from .email_channel import fetch_email_session_row, _quickcep_scripts_dir
    except Exception:
        return {"orders": [], "intention_tags": [], "source": "error", "error": "email_channel import failed"}

    scripts = _quickcep_scripts_dir()
    if str(scripts) not in _sys.path:
        _sys.path.insert(0, str(scripts))
    try:
        import quickcep_cli as qc  # type: ignore
    except ImportError:
        return {"orders": [], "intention_tags": [], "source": "error", "error": "quickcep_cli unavailable"}

    # 1. Get userUUID from session row
    row = fetch_email_session_row(quickcep_session_id)
    if not row:
        return {"orders": [], "intention_tags": [], "source": "error", "error": "session not found in email channel"}

    vi = row.get("visitorInfo") if isinstance(row.get("visitorInfo"), dict) else {}
    raw_tags = row.get("intentionTags")
    if isinstance(raw_tags, str):
        intention_tags = [raw_tags.strip()] if raw_tags.strip() else []
    elif isinstance(raw_tags, (list, tuple)):
        intention_tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        intention_tags = []
    user_uuid = vi.get("userUUID")
    customer_email = vi.get("email")

    # PR1.2: backfill visitor name/locale + intentionTags on the CAL session row.
    try:
        visitor_name = None
        for key in ("firstName", "lastName", "nickname", "name"):
            val = str(vi.get(key) or "").strip()
            if val:
                visitor_name = val
                break
        visitor_locale = str(vi.get("country") or vi.get("locale") or "").strip() or None
        enrich_session(
            quickcep_session_id=quickcep_session_id,
            env=env,
            customer_name=visitor_name,
            customer_company=None,
            locale=visitor_locale,
            customer_email=(customer_email or None),
            intention_tags=intention_tags or None,
        )
    except Exception:
        log.debug("enrich_session side effect failed for %s", quickcep_session_id, exc_info=True)

    if not user_uuid:
        return {
            "orders": [],
            "userUUID": None,
            "customer_email": customer_email,
            "intention_tags": intention_tags,
            "source": "error",
            "error": "no userUUID",
        }

    # 2. Call getOrderList
    try:
        args = _ap.Namespace(token=None, email=None, password=None)
        jwt = qc.get_jwt(args)
    except Exception:
        return {
            "orders": [],
            "userUUID": user_uuid,
            "customer_email": customer_email,
            "intention_tags": intention_tags,
            "source": "error",
            "error": "QuickCEP auth failed",
        }

    try:
        body = {"storeId": 3371, "userUUID": user_uuid}
        result = qc.api_request("POST", "/cdp-analysis/api/store/platform/data/getOrderList", jwt, body, timeout=15)
        data = result.get("data", {})
        order_list = data.get("list", []) if isinstance(data, dict) else []
    except Exception as exc:
        return {
            "orders": [],
            "userUUID": user_uuid,
            "customer_email": customer_email,
            "intention_tags": intention_tags,
            "source": "error",
            "error": str(exc)[:200],
        }

    # 3. Filter to relevant fields
    orders = []
    for od in order_list[:10]:
        if not isinstance(od, dict):
            continue
        line_items = []
        for item in (od.get("lineItems") or [])[:5]:
            if isinstance(item, dict):
                line_items.append({
                    "title": item.get("title", ""),
                    "price": item.get("price", ""),
                    "quantity": item.get("quantity", 1),
                    "productUrl": item.get("productUrl", ""),
                    "imageSrc": item.get("imageSrc", ""),
                })
        orders.append({
            "orderId": od.get("orderId", ""),
            "totalPrice": od.get("totalPrice", ""),
            "currency": od.get("currency", "USD"),
            "financialStatus": od.get("financialStatus", ""),
            "fulfillmentStatus": od.get("fulfillmentStatus", ""),
            "createDate": od.get("createDate", ""),
            "lineItems": line_items,
        })

    return {
        "orders": orders,
        "userUUID": user_uuid,
        "customer_email": customer_email,
        "intention_tags": intention_tags,
        "source": "getOrderList" if orders else "empty",
    }


def get_dispatch_context(*, quickcep_session_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    with _connect() as conn:
        sess = conn.execute(
            "SELECT * FROM cs_session WHERE quickcep_session_id=? AND env=?",
            (quickcep_session_id, env),
        ).fetchone()
        if not sess:
            return None
        sid = sess["id"]
        facts = conn.execute(
            "SELECT namespace, fact_key, fact_value_json FROM cs_facts WHERE session_id=? AND env=?",
            (sid, env),
        ).fetchall()
        events = conn.execute(
            """SELECT event_type, payload_json, created_at FROM cs_conversation_events
               WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 20""",
            (sid, env),
        ).fetchall()
        esc = conn.execute(
            """SELECT * FROM cs_escalations WHERE session_id=? AND env=?
               ORDER BY id DESC LIMIT 1""",
            (sid, env),
        ).fetchone()
        facts_map: dict[str, dict[str, Any]] = {}
        for f in facts:
            ns = f["namespace"]
            facts_map.setdefault(ns, {})[f["fact_key"]] = json.loads(f["fact_value_json"])

        # Best-effort order context injection (never blocks dispatch)
        try:
            order_ctx = _fetch_visitor_orders(quickcep_session_id)
        except Exception:
            order_ctx = {"orders": [], "intention_tags": [], "source": "error", "error": "unexpected failure"}

        intention_tags = [
            str(tag).strip()
            for tag in (order_ctx.get("intention_tags") or [])
            if str(tag).strip()
        ]
        orders = order_ctx.get("orders") or []
        should_prefill_tracking = bool(orders) and ("物流咨询" in intention_tags)
        tracking_ctx: dict[str, Any] = {
            "enabled": False,
            "source": "order-track-api",
            "reason": "intent_not_logistics_or_no_orders",
            "summaries": [],
            "errors": [],
            "circuitOpen": False,
        }

        _debug_log(
            run_id="dispatch-context",
            hypothesis_id="H2",
            location="cal.py:get_dispatch_context",
            message="dispatch-context order and intent snapshot",
            data={
                "quickcep_session_id": quickcep_session_id,
                "orders_count": len(orders) if isinstance(orders, list) else 0,
                "intention_tags": intention_tags[:5],
                "should_prefill_tracking": should_prefill_tracking,
                "order_source": str(order_ctx.get("source") or ""),
            },
        )

        if should_prefill_tracking:
            try:
                from .order_tracking import fetch_tracking_prefill

                order_ids = [
                    str(item.get("orderId") or "").strip()
                    for item in orders
                    if isinstance(item, dict) and str(item.get("orderId") or "").strip()
                ]
                tracking_ctx = fetch_tracking_prefill(order_ids)
            except Exception as exc:
                tracking_ctx = {
                    "enabled": False,
                    "source": "order-track-api",
                    "reason": "prefill_exception",
                    "summaries": [],
                    "errors": [str(exc)[:120]],
                    "circuitOpen": False,
                }
                log.warning("dispatch tracking prefill failed session=%s: %s", quickcep_session_id, exc)

        _debug_log(
            run_id="dispatch-context",
            hypothesis_id="H3",
            location="cal.py:get_dispatch_context",
            message="dispatch-context tracking prefill result",
            data={
                "quickcep_session_id": quickcep_session_id,
                "prefill_enabled": bool(tracking_ctx.get("enabled")),
                "summary_count": len(tracking_ctx.get("summaries") or []),
                "error_count": len(tracking_ctx.get("errors") or []),
                "reason": str(tracking_ctx.get("reason") or ""),
                "circuit_open": bool(tracking_ctx.get("circuitOpen")),
            },
        )

        return {
            "session": dict(sess),
            "facts": facts_map,
            "recent_events": [
                {
                    "event_type": e["event_type"],
                    "payload": json.loads(e["payload_json"]),
                    "created_at": e["created_at"],
                }
                for e in events
            ],
            "latest_escalation": dict(esc) if esc else None,
            "orders": order_ctx,
            "tracking": tracking_ctx,
        }


def write_event(
    *,
    quickcep_session_id: str,
    event_type: str,
    payload: Optional[Mapping[str, Any]] = None,
    env: str = "LIVE",
) -> bool:
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return False
    safe_payload = sanitize_mapping(dict(payload or {}))
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at)
               VALUES (?,?,?,?,?)""",
            (sess["id"], event_type, json.dumps(safe_payload), env, _now()),
        )
        conn.commit()
    return True


def write_facts(
    *,
    quickcep_session_id: str,
    namespaces: Mapping[str, Mapping[str, Any]],
    env: str = "LIVE",
) -> bool:
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return False
    safe_namespaces, _ = sanitize_namespaces(namespaces)
    now = _now()
    sid = sess["id"]
    with _connect() as conn:
        for ns, kv in safe_namespaces.items():
            for key, val in kv.items():
                conn.execute(
                    """INSERT INTO cs_facts(session_id, namespace, fact_key, fact_value_json, env, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(session_id, namespace, fact_key, env) DO UPDATE SET
                           fact_value_json=excluded.fact_value_json,
                           updated_at=excluded.updated_at""",
                    (sid, ns, key, json.dumps(val), env, now, now),
                )
        conn.commit()
    return True


def open_escalation(
    *,
    quickcep_session_id: str,
    reason: str,
    urgency: str = "medium",
    question_to_operator: Optional[str] = None,
    feishu_chat_id: Optional[str] = None,
    feishu_thread_id: Optional[str] = None,
    feishu_message_id: Optional[str] = None,
    resume_context: Optional[Mapping[str, Any]] = None,
    env: str = "LIVE",
) -> Optional[int]:
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    now = _now()
    safe_resume = sanitize_mapping(dict(resume_context or {}))
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO cs_escalations(
                   session_id, reason, urgency, state, question_to_operator,
                   feishu_chat_id, feishu_thread_id, feishu_message_id,
                   resume_context_json, env, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sess["id"],
                reason,
                urgency,
                "awaiting_answer",
                mask_string(question_to_operator) if question_to_operator else None,
                feishu_chat_id,
                feishu_thread_id,
                feishu_message_id,
                json.dumps(safe_resume),
                env,
                now,
                now,
            ),
        )
        # Lifecycle status is owned by apply-handoff (awaiting_expert phase), not open_escalation.
        conn.commit()
        return int(cur.lastrowid)


def update_escalation_feishu(
    *,
    escalation_id: int,
    feishu_chat_id: Optional[str] = None,
    feishu_thread_id: Optional[str] = None,
    feishu_message_id: Optional[str] = None,
) -> bool:
    """Persist Feishu delivery ids after bridge sends escalation notify."""
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE cs_escalations SET
                   feishu_chat_id=COALESCE(?, feishu_chat_id),
                   feishu_thread_id=COALESCE(?, feishu_thread_id),
                   feishu_message_id=COALESCE(?, feishu_message_id),
                   updated_at=?
               WHERE id=?""",
            (feishu_chat_id, feishu_thread_id, feishu_message_id, now, escalation_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_escalation(*, escalation_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cs_escalations WHERE id=?", (escalation_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["resume_context"] = json.loads(out.pop("resume_context_json") or "{}")
        sess = conn.execute("SELECT * FROM cs_session WHERE id=?", (out["session_id"],)).fetchone()
        out["session"] = dict(sess) if sess else None
        return out


def list_escalations(*, state: Optional[str] = None, env: str = "LIVE") -> list[dict[str, Any]]:
    with _connect() as conn:
        if state:
            rows = conn.execute(
                "SELECT * FROM cs_escalations WHERE state=? AND env=? ORDER BY id DESC",
                (state, env),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cs_escalations WHERE env=? ORDER BY id DESC LIMIT 100",
                (env,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["resume_context"] = json.loads(item.pop("resume_context_json") or "{}")
            sess = conn.execute(
                "SELECT quickcep_session_id, customer_email, status FROM cs_session WHERE id=?",
                (item["session_id"],),
            ).fetchone()
            if sess:
                item["quickcep_session_id"] = sess["quickcep_session_id"]
                item["customer_email"] = sess["customer_email"]
                item["session_status"] = sess["status"]
            out.append(item)
        return out


def list_escalations_for_session(
    *,
    quickcep_session_id: str,
    states: tuple[str, ...] | None = None,
    env: str = "LIVE",
) -> list[dict[str, Any]]:
    """Return escalation rows for one QuickCEP session, optionally filtered by state."""
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return []
    with _connect() as conn:
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = conn.execute(
                f"""SELECT * FROM cs_escalations
                    WHERE session_id=? AND env=? AND state IN ({placeholders})
                    ORDER BY id DESC""",
                (sess["id"], env, *states),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM cs_escalations
                   WHERE session_id=? AND env=?
                   ORDER BY id DESC""",
                (sess["id"], env),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["resume_context"] = json.loads(item.pop("resume_context_json") or "{}")
            item["quickcep_session_id"] = quickcep_session_id
            item["session_status"] = sess.get("status")
            out.append(item)
        return out


def get_resuming_escalation_for_session(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
) -> Optional[dict[str, Any]]:
    """Return the in-flight escalation resume row for a QuickCEP session, if any."""
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM cs_escalations
               WHERE session_id=? AND env=? AND state='resuming'
               ORDER BY id DESC LIMIT 1""",
            (sess["id"], env),
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["resume_context"] = json.loads(out.pop("resume_context_json") or "{}")
        out["session"] = dict(sess)
        return out


def claim_escalation_reply(
    *,
    escalation_id: int,
    operator_answer: str,
    decided_by: str,
    feishu_reply_message_id: str,
) -> bool:
    """Atomically accept the first operator reply (awaiting_answer → resuming)."""
    answer = (operator_answer or "").strip()
    if not answer:
        return False
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_context_json FROM cs_escalations WHERE id=? AND state='awaiting_answer'",
            (escalation_id,),
        ).fetchone()
        if not row:
            return False
        ctx = json.loads(row["resume_context_json"] or "{}")
        ctx["feishu_reply_message_id"] = feishu_reply_message_id
        ctx["claimed_at"] = now
        ctx["operator_answer_raw"] = answer
        cur = conn.execute(
            """UPDATE cs_escalations SET
                   state='resuming',
                   operator_answer=?,
                   decided_by=?,
                   decision='claim',
                   decided_at=?,
                   resume_context_json=?,
                   updated_at=?
               WHERE id=? AND state='awaiting_answer'""",
            (mask_string(answer), decided_by, now, json.dumps(ctx), now, escalation_id),
        )
        conn.commit()
        return cur.rowcount == 1


def record_escalation_resume_run(*, escalation_id: int, run_id: str) -> bool:
    """Persist gateway run id while escalation stays in resuming until handoff completes."""
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_context_json FROM cs_escalations WHERE id=? AND state='resuming'",
            (escalation_id,),
        ).fetchone()
        if not row:
            return False
        ctx = json.loads(row["resume_context_json"] or "{}")
        ctx["resume_run_id"] = run_id
        ctx["resume_launched_at"] = now
        cur = conn.execute(
            """UPDATE cs_escalations SET resume_context_json=?, updated_at=?
               WHERE id=? AND state='resuming'""",
            (json.dumps(ctx), now, escalation_id),
        )
        conn.commit()
        return cur.rowcount == 1


def finalize_escalation(
    *,
    escalation_id: int,
    decision: str = "completed",
    final_state: str = "resolved",
) -> bool:
    """Close a resuming escalation without changing cs_session status."""
    if final_state not in ESCALATION_STATES:
        raise ValueError(f"invalid final_state: {final_state}")
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE cs_escalations SET
                   decision=?, decided_at=?, state=?, updated_at=?
               WHERE id=? AND state='resuming'""",
            (decision, now, final_state, now, escalation_id),
        )
        conn.commit()
        return cur.rowcount == 1


def merge_escalation_resume_context(*, escalation_id: int, patch: Mapping[str, Any]) -> bool:
    """Merge keys into resume_context_json for an escalation row."""
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_context_json FROM cs_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
        if not row:
            return False
        ctx = json.loads(row["resume_context_json"] or "{}")
        ctx.update(dict(patch))
        cur = conn.execute(
            "UPDATE cs_escalations SET resume_context_json=?, updated_at=? WHERE id=?",
            (json.dumps(ctx), now, escalation_id),
        )
        conn.commit()
        return cur.rowcount == 1


def resolve_escalation(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
    final_state: str = "resolved",
    touch_session: bool = True,
) -> bool:
    if final_state not in ESCALATION_STATES:
        raise ValueError(f"invalid final_state: {final_state}")
    now = _now()
    masked_answer = mask_string(operator_answer) if operator_answer else None
    with _connect() as conn:
        row = conn.execute("SELECT session_id FROM cs_escalations WHERE id=?", (escalation_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE cs_escalations SET
                   decision=?, decided_by=?, decided_at=?, operator_answer=COALESCE(?, operator_answer),
                   state=?, updated_at=?
               WHERE id=?""",
            (decision, decided_by, now, masked_answer, final_state, now, escalation_id),
        )
        if final_state == "resolved" and touch_session:
            conn.execute(
                "UPDATE cs_session SET status='processing', updated_at=? WHERE id=?",
                (now, row["session_id"]),
            )
        conn.commit()
    return True


def patch_escalation_decision(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
) -> bool:
    """Update decision metadata after finalize (e.g. manual resolve overlay)."""
    now = _now()
    masked_answer = mask_string(operator_answer) if operator_answer else None
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE cs_escalations SET
                   decision=?, decided_by=?, decided_at=?,
                   operator_answer=COALESCE(?, operator_answer),
                   updated_at=?
               WHERE id=?""",
            (decision, decided_by, now, masked_answer, now, escalation_id),
        )
        conn.commit()
        return cur.rowcount == 1


# ── ESC attachment vault ─────────────────────────────────────────────


def get_vault_blob(md5: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM vault_blob WHERE md5=?", (md5,)).fetchone()
        return dict(row) if row else None


def insert_vault_blob(
    *,
    md5: str,
    stored_path: str,
    size_bytes: int,
    content_type: Optional[str],
    kind: str,
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO vault_blob(md5, stored_path, size_bytes, content_type, kind, ref_count, created_at)
               VALUES (?,?,?,?,?,0,?)""",
            (md5, stored_path, size_bytes, content_type, kind, now),
        )
        conn.commit()


def set_vault_blob_cdn_url(*, md5: str, cdn_url: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE vault_blob SET cdn_url=?, cdn_uploaded_at=? WHERE md5=?",
            (cdn_url, now, md5),
        )
        conn.commit()


def insert_vault_link(
    *,
    link_id: str,
    escalation_id: int,
    blob_md5: str,
    original_name: str,
    uploaded_by: Optional[str] = None,
) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO escalation_vault_link(id, escalation_id, blob_md5, original_name, uploaded_at, uploaded_by)
               VALUES (?,?,?,?,?,?)""",
            (link_id, escalation_id, blob_md5, original_name, now, uploaded_by),
        )
        conn.execute(
            "UPDATE vault_blob SET ref_count = ref_count + 1 WHERE md5=?",
            (blob_md5,),
        )
        conn.commit()


def list_vault_links_for_escalation(*, escalation_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT l.*, b.kind, b.cdn_url, b.size_bytes, b.content_type, b.stored_path
               FROM escalation_vault_link l
               JOIN vault_blob b ON b.md5 = l.blob_md5
               WHERE l.escalation_id=?
               ORDER BY l.uploaded_at ASC""",
            (escalation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_vault_link(*, link_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT blob_md5 FROM escalation_vault_link WHERE id=?",
            (link_id,),
        ).fetchone()
        if not row:
            return False
        blob_md5 = row["blob_md5"]
        cur = conn.execute("DELETE FROM escalation_vault_link WHERE id=?", (link_id,))
        if cur.rowcount:
            conn.execute(
                "UPDATE vault_blob SET ref_count = CASE WHEN ref_count > 0 THEN ref_count - 1 ELSE 0 END WHERE md5=?",
                (blob_md5,),
            )
        conn.commit()
        return cur.rowcount == 1


def list_stale_vault_links(*, escalation_resolved_before: str) -> list[dict[str, Any]]:
    """Links on escalations in terminal states before cutoff timestamp."""
    terminal = ("resolved", "aborted", "re_escalated")
    placeholders = ",".join("?" for _ in terminal)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT l.id, l.escalation_id, l.blob_md5
               FROM escalation_vault_link l
               JOIN cs_escalations e ON e.id = l.escalation_id
               WHERE e.state IN ({placeholders})
                 AND e.updated_at < ?""",
            (*terminal, escalation_resolved_before),
        ).fetchall()
        return [dict(r) for r in rows]


def list_orphan_vault_blobs(*, created_before: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM vault_blob WHERE ref_count <= 0 AND created_at < ?",
            (created_before,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_vault_blob(*, md5: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM vault_blob WHERE md5=?", (md5,))
        conn.commit()
        return cur.rowcount == 1


def get_poller_state(poller_name: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT state_json FROM cs_poller_state WHERE poller_name=?",
            (poller_name,),
        ).fetchone()
        if not row:
            return {}
        return json.loads(row["state_json"] or "{}")


def set_poller_state(poller_name: str, state: Mapping[str, Any]) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cs_poller_state(poller_name, state_json, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(poller_name) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
            (poller_name, json.dumps(dict(state)), now),
        )
        conn.commit()
