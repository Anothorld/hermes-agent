"""SQLite CAL for cs-ops-bridge."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
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
# PR3: statuses that may be overridden by enqueue_session(force_status=...).
# Busy statuses (processing/awaiting_expert/draft_ready/operator_replied/reviewed)
# are excluded so an in-flight session is never disrupted by a skip event.
_SKIPPABLE_STATUSES = frozenset({"pending", "failed", "skipped"})

# Monotonic lifecycle ranks — mirror session_handoff._STATUS_ORDER. Used by
# update_session_status(allow_regression=False) to forbid accidental status
# regressions from direct callers that bypass the apply_handoff guard. A
# regression is a target rank strictly lower than the current rank. `pending`
# (rank 0) is the floor so resetting to pending always "regresses" and must
# pass allow_regression=True (the watcher's rollback paths do this).
_STATUS_RANKS: dict[str, int] = {
    "pending": 0,
    "processing": 10,
    "failed": 15,
    "awaiting_expert": 20,
    "skipped": 25,
    "draft_ready": 30,
    "operator_replied": 40,
    "reviewed": 50,
}


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
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.replace("\xa0", " ").strip()
    return not text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_schema_initialized = False
# Track WHICH db path was initialized. _schema_initialized is process-global,
# but _DB_PATH is per-test (tests monkeypatch it to a fresh tmp_path). Without
# this, the second test's new DB never gets recreate_all → "no such table".
# Also guards against a runtime DB path swap (e.g. profile switch).
_initialized_db_path: Path | None = None


def _connect() -> sqlite3.Connection:
    global _schema_initialized, _initialized_db_path
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    # busy_timeout: wait up to 5s for a lock instead of raising SQLITE_BUSY
    # immediately. WAL mode allows concurrent readers, but DDL/PRAGMA still
    # need a brief write lock; without this, requests fail under contention.
    conn.execute("PRAGMA busy_timeout=5000")
    # journal_mode=WAL is persistent — only set it once per DB file to avoid
    # competing for the write lock on every connection open. Re-run recreate_all
    # (CREATE TABLE IF NOT EXISTS — no-op on an already-initialized DB) when the
    # path changes, so a fresh DB gets its tables.
    if not _schema_initialized or _initialized_db_path != _DB_PATH:
        conn.execute("PRAGMA journal_mode=WAL")
        recreate_all(conn)
        _schema_initialized = True
        _initialized_db_path = _DB_PATH
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
    force_status: Optional[str] = None,
    skip_event_payload: Optional[dict[str, Any]] = None,
    set_processing: bool = False,
) -> dict[str, Any]:
    """Idempotent enqueue; returns ``created`` flag and session row.

    Optional visitor/draft-preview fields (PR1.2) are persisted with COALESCE
    so a re-enqueue for a follow-up message never wipes previously stored values.

    ``force_status`` (PR3): when set (e.g. ``"skipped"`` for permanent inbound
    skips), the session status is forced to that value **only if the current
    status is skippable** (pending/failed/skipped). Busy statuses
    (processing/awaiting_expert/draft_ready/operator_replied/reviewed) are
    preserved so an in-flight session is never disrupted by a skip event.
    When ``force_status`` is set, the event written is ``inbound_skipped``
    (payload merged with ``skip_event_payload``) instead of the default
    ``inbound_received`` / ``customer_followup_while_busy``.

    ``set_processing``: when True and the session ends up in ``pending``
    (i.e. ``should_launch`` would be True), the status is atomically set to
    ``processing`` within this same transaction. This eliminates the crash
    window between the dedup-row write and the separate
    ``update_session_status("processing")`` call the watcher used to make —
    if the process died between them, the dedup row would block future
    re-enqueue of the same message_id. With this flag the watcher no longer
    needs the separate call; the row is created/updated + processing-stamped
    + dedup-inserted in one commit. The returned ``should_launch`` stays
    True so the caller still launches the gateway run.
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
            log.info(
                "cs.intake session=%s env=%s message_id=%s decision=deduped "
                "(already processed)",
                quickcep_session_id, env, message_id,
            )
            return {"created": False, "deduped": True, "should_launch": False, "session": dict(row) if row else None}

        # Capture prior status before UPSERT so the inbound event can flag a
        # reopen (terminal status rolled back to pending by a follow-up message).
        prior_row = conn.execute(
            "SELECT status FROM cs_session WHERE quickcep_session_id=? AND env=?",
            (quickcep_session_id, env),
        ).fetchone()
        prior_status = str(prior_row[0]) if prior_row else None
        is_reopen = prior_status in (
            "draft_ready", "awaiting_expert", "operator_replied", "failed", "reviewed",
        )

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
                   status=CASE WHEN status IN ('draft_ready','awaiting_expert','operator_replied','failed','reviewed') THEN 'pending'
                           WHEN status = 'skipped' THEN 'skipped'
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

        # PR3: force_status busy guard. Only override when the current status
        # is skippable (idle/already-skipped); never disrupt an in-flight session.
        if force_status and status in _SKIPPABLE_STATUSES:
            conn.execute(
                "UPDATE cs_session SET status=?, updated_at=? WHERE id=?",
                (force_status, now, session["id"]),
            )
            status = force_status
            session["status"] = force_status

        should_launch = status == "pending"
        # Atomic processing transition: when the caller asked to set processing
        # AND the session is in a launchable state, stamp `processing` in this
        # same transaction so the dedup row + processing status commit together.
        # The returned should_launch stays True so the caller still launches.
        if set_processing and should_launch:
            # Fresh processing cycle: stamp processing_started_at to NOW (not
            # COALESCE) and clear agent_processing_at. A reopened session
            # (terminal → pending → processing) is a NEW cycle — the old
            # processing_started_at/agent_processing_at anchors belong to the
            # prior cycle and would mislead processing_stale's heartbeat. The
            # never-confirmed heartbeat fires when agent_processing_at IS NULL
            # + processing_started_at ages past _HEARTBEAT_MIN, so the anchor
            # must reflect this cycle's start.
            conn.execute(
                "UPDATE cs_session SET status='processing', "
                "processing_started_at=?, "
                "agent_processing_at=NULL, "
                "updated_at=? WHERE id=?",
                (now, now, session["id"]),
            )
            status = "processing"
            session["status"] = "processing"
        if force_status:
            event_type = "inbound_skipped"
            event_payload = {
                "message_id": message_id,
                "status": status,
                "is_reopen": is_reopen,
                "prior_status": prior_status,
            }
            if skip_event_payload:
                event_payload.update(skip_event_payload)
        else:
            event_type = "inbound_received"
            if not should_launch:
                event_type = "customer_followup_while_busy"
            event_payload = {
                "message_id": message_id,
                "status": status,
                "is_reopen": is_reopen,
                "prior_status": prior_status,
            }
        conn.execute(
            """INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at)
               VALUES (?,?,?,?,?)""",
            (
                session["id"],
                event_type,
                # PR3: sanitize — inbound_skipped payloads may carry sender/subject PII.
                json.dumps(sanitize_mapping(event_payload)),
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
        # Audit: inbound intake decision. Captures the full decision context —
        # whether this is a new session or a reopen (terminal→pending), whether
        # the watcher should launch AI (should_launch), any force_status skip,
        # and the resulting status. This is the entry point of the state flow.
        log.info(
            "cs.intake session=%s env=%s message_id=%s decision=created "
            "prior_status=%s status=%s should_launch=%s is_reopen=%s "
            "force_status=%s set_processing=%s event_type=%s",
            quickcep_session_id, env, message_id, prior_status, status,
            should_launch, is_reopen, force_status or "-", set_processing,
            event_type,
        )
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


def _sessions_list_filters(
    *,
    env: str,
    status: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> tuple[str, list[Any]]:
    """Shared WHERE clause + params for session list/count queries.

    ``status`` accepts a comma-separated list (e.g. ``"operator_replied,reviewed"``)
    so the workbench can fetch all CAL statuses that map to one display filter
    (operator = operator_sent/operator_replied/reviewed; skipped = skipped/failed).
    """
    sql = " FROM cs_session WHERE env=?"
    params: list[Any] = [env]
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            sql += " AND status=?"
            params.append(statuses[0])
        elif statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (quickcep_session_id LIKE ? OR customer_email LIKE ? OR chat_session_id LIKE ?)"
        params.extend([like, like, like])
    if since:
        sql += " AND COALESCE(processing_started_at, created_at) >= ?"
        params.append(since)
    if until:
        sql += " AND COALESCE(processing_started_at, created_at) < ?"
        params.append(until)
    return sql, params


def count_sessions(
    *,
    env: str = "LIVE",
    status: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    """Count sessions matching the same filters as ``list_sessions``."""
    where, params = _sessions_list_filters(
        env=env, status=status, q=q, since=since, until=until,
    )
    with _connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n{where}", params).fetchone()
    return int(row["n"]) if row else 0


def list_sessions(
    *,
    env: str = "LIVE",
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List sessions with optional server-side filtering.

    ``since`` / ``until`` are inclusive-exclusive ISO bounds applied to the
    session's first-active timestamp. v5 prefers ``processing_started_at``
    (stamped the first time a session leaves ``pending``) and falls back to
    ``created_at`` for pre-v5 rows that never got stamped. This lets the daily
    report bucket sessions by the day they were actually worked instead of by
    the volatile ``updated_at`` (which flips on every follow-up / retry).
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where, params = _sessions_list_filters(
        env=env, status=status, q=q, since=since, until=until,
    )
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT *{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
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


def session_display_counts(*, env: str = "LIVE") -> dict[str, int]:
    """Counts grouped by the *frontend display status* the workbench chips use.

    Mirrors ``classify_intent``-side ``mapStatus``: ``draft_ready`` splits into
    ``autopilot`` (latest autopilot job scheduled) vs ``draft``; ``operator`` =
    operator_sent/operator_replied/reviewed; ``skipped`` and ``failed`` are
    reported as separate buckets so the workbench can show a distinct
    「处理失败」chip; everything else collapses to ``processing``. ``all`` is
    the grand total.

    This is the authoritative source for chip badges so they stay correct
    regardless of which filter is active or how many rows are loaded.
    """
    sql = """
        SELECT
          CASE
            WHEN s.status='draft_ready' AND (
              SELECT j.status FROM cs_autopilot_jobs j
              WHERE j.session_id=s.id AND j.env=s.env
              ORDER BY j.id DESC LIMIT 1
            )='scheduled' THEN 'autopilot'
            WHEN s.status='draft_ready' THEN 'draft'
            WHEN s.status='awaiting_expert' THEN 'escalating'
            WHEN s.status IN ('operator_sent','operator_replied','reviewed') THEN 'operator'
            WHEN s.status='failed' THEN 'failed'
            WHEN s.status='skipped' THEN 'skipped'
            ELSE 'processing'
          END AS display,
          COUNT(*) AS n
        FROM cs_session s
        WHERE s.env=?
        GROUP BY display
    """
    with _connect() as conn:
        rows = conn.execute(sql, (env,)).fetchall()
    # Frontend chips expect every display-status key to be present. GROUP BY only
    # emits groups with rows, so a 0-count status (e.g. no autopilot sessions)
    # would be missing and the chip badge would keep its stale HTML placeholder.
    counts = {k: 0 for k in ("draft", "autopilot", "operator", "escalating", "skipped", "failed", "processing")}
    for r in rows:
        counts[r["display"]] = int(r["n"])
    counts["all"] = sum(v for k, v in counts.items() if k != "all")
    return counts



def escalations_in_window(
    *,
    env: str = "LIVE",
    since: str,
    until: str,
) -> dict[str, Any]:
    """Escalations created in [since, until) — the daily-report-correct count.

    The daily report previously counted ``status == 'awaiting_expert'`` as
    "升级到专家", which is a *snapshot* of currently-pending escalations and
    misses every escalation that was already answered the same day. Counting
    ``cs_escalations.created_at`` in the report window instead captures every
    escalation that fired that day regardless of whether the expert has since
    replied.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT e.*, s.quickcep_session_id, s.customer_email, s.customer_name
               FROM cs_escalations e
               INNER JOIN cs_session s ON s.id = e.session_id AND s.env = e.env
               WHERE e.env=? AND e.created_at >= ? AND e.created_at < ?
               ORDER BY e.created_at ASC""",
            (env, since, until),
        ).fetchall()
    items = [dict(r) for r in rows]
    return {"count": len(items), "items": items}


def draft_saved_session_ids(
    *,
    env: str = "LIVE",
    since: str,
    until: str,
) -> set[int]:
    """CAL row ids of sessions that had a ``draft_saved`` event in [since, until).

    Used by the daily report as a fallback signal for "AI generated a draft"
    when ``draft_source`` is missing (a known tracking bug — some runs saved a
    draft but never stamped ``cs_session.draft_source``). Cross-referencing the
    event ledger catches those runs so AI-draft counts are not under-reported.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT e.session_id FROM cs_conversation_events e
               INNER JOIN cs_session s ON s.id = e.session_id AND s.env = e.env
               WHERE e.env=? AND e.event_type='draft_saved'
                 AND e.created_at >= ? AND e.created_at < ?""",
            (env, since, until),
        ).fetchall()
    return {int(r["session_id"]) for r in rows}


def daily_report_stats(
    *,
    env: str = "LIVE",
    since: str,
    until: str,
    limit: int = 200,
) -> dict[str, Any]:
    """One-shot aggregate the daily report needs — avoids N+1 / multi-call drift.

    Bundles three reads the daily report used to do separately (and
    inconsistently): the processed-session window (server-side date filter +
    full limit, not the legacy client-side `updated_at >= today` over a 50-row
    page), the escalation count by ``created_at`` (not by snapshot status),
    and the ``draft_saved`` event set for ``draft_source`` fallback. The report
    script makes a single call and gets a consistent snapshot.

    PR3: permanent inbound skips (non_email/blocklist/intention_not_allowed/ad)
    now create CAL rows with ``status=skipped``. The daily report skill defines
    "processed" as ``status NOT IN (pending, failed)`` which would incorrectly
    count skipped rows as processed. We filter them out server-side so the
    headline "processed" metric is not inflated by skip audit rows. Direct SQL
    queries on cs_session still see skipped rows (use ``status != 'skipped'``
    when computing processed counts off the raw table).
    """
    all_sessions = list_sessions(env=env, since=since, until=until, limit=limit)
    sessions = [s for s in all_sessions if s.get("status") != "skipped"]
    escalations = escalations_in_window(env=env, since=since, until=until)
    draft_saved_ids = draft_saved_session_ids(env=env, since=since, until=until)
    return {
        "since": since,
        "until": until,
        "env": env,
        "sessions": sessions,
        "escalations": escalations,
        "draft_saved_session_ids": sorted(draft_saved_ids),
    }


# ── Metrics trend (Console 数据页签) ──────────────────────────────────────
# All timestamps in CAL are stored as UTC ISO8601 (see _now()). To bucket by
# Beijing natural day (Asia/Shanghai, UTC+8, no DST) we shift with SQLite's
# ``+8 hours`` modifier on the naive UTC value: ``date(datetime(ts, '+8 hours'))``.
# This is validated against real data and matches the operator's "当日" intent.
#
# Metric formulas (audit-locked, see docs/features/metrics/GUIDE.md):
#   ① AI 承担率
#        分子 = DISTINCT session_id of draft_saved events with
#                payload.source IN ('agent','resume_agent')  (excludes operator_edit)
#        分母 = 需回复工单 = 当日已分类会话 − spam 会话 (来自 cs-intent-classifier,
#                由 Console 后端合并;广告在分类前被 bridge 跳过,不在分类会话内)
#        gap = 进入生成 − 当日出草稿,按去向分类(failed/takeover/escalated/in_flight/other)
#   ② 生成升级率
#        分母 = DISTINCT session_id where
#                COALESCE(agent_processing_at, processing_started_at) 当日  (= "AI 处理工单" 共同基数)
#        分子 = DISTINCT session_id of cs_escalations where created_at 当日
#                AND EXISTS session_handoff phase='processing' before esc.created_at
#   ④ AI 直接交付率
#        分子 = DISTINCT session_id where sent_draft_source IN ('agent','resume_agent') 当日
#        分母 = 当日 AI 草稿会话数(同①分子)
# (agent_processing_at is v6+; fall back to processing_started_at for v5 rows.)
#
# "AI 处理工单" = entered_generation (②分母) 是 ①②④ 的共同基数口径来源;
# ①分子(草稿)是它的子集,gap 展示进入生成却未出草稿的构成。


def _bj_date(ts_col: str) -> str:
    """SQLite expression yielding Beijing natural date (YYYY-MM-DD) from a UTC ISO ts."""
    return f"date(datetime({ts_col}, '+8 hours'))"


# Maps AI classifier primary_intent → deterministic gate classify category.
# The gate stores its category in cs_facts(namespace='classify', fact_key='category').
# Only the intents exposed in the Console trend filter are listed here.
_INTENT_TO_GATE_CATEGORY: dict[str, str] = {
    "logistics_inquiry": "logistics",
    "product_inquiry": "product",
}


def _build_intent_filter(intent: str | None, env: str) -> tuple[str, str, tuple[str, ...]]:
    """Return (s_filter, e_filter, params) for SQL WHERE clauses.

    ``s_filter`` is appended to queries that alias cs_session as ``s``
    (gen_rows, sent_rows). ``e_filter`` is appended to queries that reference
    session_id via ``e.session_id`` (draft_rows, esc_rows). When ``intent``
    is None or unmapped, both filters are empty strings and params is empty.
    """
    if not intent:
        return "", "", ()
    category = _INTENT_TO_GATE_CATEGORY.get(intent)
    if not category:
        return "", "", ()
    cat_json = json.dumps(category, ensure_ascii=False)
    # Both filters use the same param order: (cat_json, env).
    # s_filter: for queries with cs_session aliased as s (s.id is the session PK).
    s_filter = (
        " AND s.id IN ("
        " SELECT session_id FROM cs_facts"
        " WHERE namespace='classify' AND fact_key='category'"
        " AND fact_value_json=? AND env=?)"
    )
    # e_filter: for queries referencing session_id via e.session_id (events / escalations).
    e_filter = (
        " AND e.session_id IN ("
        " SELECT s2.id FROM cs_session s2"
        " INNER JOIN cs_facts f ON f.session_id=s2.id AND f.env=s2.env"
        " WHERE f.namespace='classify' AND f.fact_key='category'"
        " AND f.fact_value_json=? AND s2.env=?)"
    )
    return s_filter, e_filter, (cat_json, env)


def metrics_trend(
    *,
    env: str = "LIVE",
    since: str,
    until: str,
    intent: str | None = None,
) -> dict[str, Any]:
    """Per-day metric series for the Console 数据页签, bucketed by Beijing day.

    Returns AI 承担率分子/分母构成、生成升级率、AI 直接交付率 的 CAL 侧数字。
    意图分类错误率(③)与承担率分母(classified/spam)在 cs-intent-classifier DB,
    由 Console 后端按日期合并。Days with no activity are filled with zeros so the
    frontend gets a contiguous series.

    When ``intent`` is set (e.g. ``logistics_inquiry``, ``product_inquiry``),
    sessions are filtered by the deterministic gate's classify category
    (stored in ``cs_facts`` namespace='classify', fact_key='category').
    See ``_INTENT_TO_GATE_CATEGORY`` for the mapping.
    """
    # Build intent filter clauses for queries that reference cs_session (s.id)
    # and for queries that reference session_id directly (e.session_id).
    s_filter, e_filter, filter_params = _build_intent_filter(intent, env)

    with _connect() as conn:
        # ① 分子 — 当日 AI 生成草稿会话 (DISTINCT session)
        # Bucket by the SESSION's entered-generation timestamp (COALESCE(
        # agent_processing_at, processing_started_at)), NOT the draft_saved
        # event time — so ai_draft_sessions is a strict subset of
        # entered_generation on the same day.  Previously bucketing by
        # e.created_at caused cross-day drift (session entered gen on D-1
        # 23:50, draft saved on D 00:10 → counted as different days, making
        # ai_draft > entered_generation and the ① numerator > ② denominator).
        draft_rows = conn.execute(
            f"""SELECT {_bj_date('COALESCE(s.agent_processing_at, s.processing_started_at)')} AS d,
                      e.session_id AS sid
                FROM cs_conversation_events e
                INNER JOIN cs_session s ON s.id = e.session_id AND s.env = e.env
                WHERE e.env=? AND e.event_type='draft_saved'
                  AND json_extract(e.payload_json, '$.source') IN ('agent','resume_agent')
                  AND COALESCE(s.agent_processing_at, s.processing_started_at) >= ?
                  AND COALESCE(s.agent_processing_at, s.processing_started_at) < ?{e_filter}""",
            (env, since, until, *filter_params),
        ).fetchall()
        # ② 分母 / 共同基数 — 当日进入生成流程的会话 (DISTINCT session + 其当前 status)
        gen_rows = conn.execute(
            f"""SELECT {_bj_date('COALESCE(s.agent_processing_at, s.processing_started_at)')} AS d,
                      s.id AS sid, s.status AS status
                FROM cs_session s
                WHERE s.env=?
                  AND COALESCE(s.agent_processing_at, s.processing_started_at) IS NOT NULL
                  AND COALESCE(s.agent_processing_at, s.processing_started_at) >= ?
                  AND COALESCE(s.agent_processing_at, s.processing_started_at) < ?{s_filter}""",
            (env, since, until, *filter_params),
        ).fetchall()
        # ② 分子 — 当日"生成中升级"会话 (DISTINCT session)
        esc_rows = conn.execute(
            f"""SELECT {_bj_date('e.created_at')} AS d, e.session_id AS sid
                FROM cs_escalations e
                WHERE e.env=? AND e.created_at >= ? AND e.created_at < ?
                  AND EXISTS (
                    SELECT 1 FROM cs_conversation_events ev
                    WHERE ev.session_id = e.session_id
                      AND ev.event_type='session_handoff'
                      AND json_extract(ev.payload_json, '$.phase')='processing'
                      AND ev.created_at < e.created_at
                  ){e_filter}""",
            (env, since, until, *filter_params),
        ).fetchall()
        # ④ 分子 — 当日以 AI 原稿发送的会话 (DISTINCT session)
        sent_rows = conn.execute(
            f"""SELECT {_bj_date('s.sent_draft_at')} AS d, s.id AS sid
                FROM cs_session s
                WHERE s.env=? AND s.sent_draft_source IN ('agent','resume_agent')
                  AND s.sent_draft_at IS NOT NULL
                  AND s.sent_draft_at >= ? AND s.sent_draft_at < ?{s_filter}""",
            (env, since, until, *filter_params),
        ).fetchall()

    # Index by Beijing date.
    drafted_by_day: dict[str, set[int]] = {}
    for r in draft_rows:
        drafted_by_day.setdefault(r["d"], set()).add(int(r["sid"]))
    gen_by_day: dict[str, dict[int, str]] = {}  # sid -> status
    for r in gen_rows:
        gen_by_day.setdefault(r["d"], {})[int(r["sid"])] = str(r["status"] or "")
    esc_by_day: dict[str, set[int]] = {}
    for r in esc_rows:
        esc_by_day.setdefault(r["d"], set()).add(int(r["sid"]))
    sent_by_day: dict[str, set[int]] = {}
    for r in sent_rows:
        sent_by_day.setdefault(r["d"], set()).add(int(r["sid"]))

    all_days = sorted(set(drafted_by_day) | set(gen_by_day) | set(esc_by_day) | set(sent_by_day))
    days = []
    for d in all_days:
        drafted = drafted_by_day.get(d, set())
        gen = gen_by_day.get(d, {})
        esc = esc_by_day.get(d, set())
        sent = sent_by_day.get(d, set())
        gen_sids = set(gen.keys())
        # gap: 进入生成当日 但 当日未出草稿,按去向分类
        gap = gen_sids - drafted
        gap_breakdown = {"failed": 0, "takeover": 0, "escalated": 0, "in_flight": 0, "skipped": 0, "other": 0}
        for sid in gap:
            st = gen.get(sid, "")
            if st == "failed":
                gap_breakdown["failed"] += 1
            elif st == "skipped":
                gap_breakdown["skipped"] += 1
            elif st in ("operator_replied", "operator_sent"):
                gap_breakdown["takeover"] += 1
            elif st == "awaiting_expert" or sid in esc:
                gap_breakdown["escalated"] += 1
            elif st == "processing":
                gap_breakdown["in_flight"] += 1
            else:
                gap_breakdown["other"] += 1
        days.append({
            "date": d,
            "ai_draft_sessions": len(drafted),
            "entered_generation": len(gen_sids),
            "entered_not_drafted": len(gap),
            "gap_breakdown": gap_breakdown,
            "escalated_sessions": len(esc),
            "sent_as_agent_sessions": len(sent),
        })
    return {"env": env, "since": since, "until": until, "days": days}


def escalated_quickcep_ids_by_day(
    *,
    env: str = "LIVE",
    since: str,
    until: str,
    intent: str | None = None,
) -> dict[str, set[str]]:
    """Per Beijing day → set of quickcep_session_id that escalated (生成中口径).

    Same SQL as metrics_trend's escalation numerator, but returns the id SET
    (keyed by quickcep_session_id) so the route can union with HindSight
    hit-auto ids for the ④ 减升率 denominator. Same口径 as ② → "升级" stays
    consistent across ② and ④.

    When ``intent`` is set, only escalations for sessions whose deterministic
    gate classify category matches are returned (see ``_INTENT_TO_GATE_CATEGORY``).
    """
    s_filter, _e_filter, filter_params = _build_intent_filter(intent, env)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT {_bj_date('e.created_at')} AS d,
                      s.quickcep_session_id AS qid
                FROM cs_escalations e
                INNER JOIN cs_session s ON s.id = e.session_id AND s.env = e.env
                WHERE e.env=? AND e.created_at >= ? AND e.created_at < ?{s_filter}
                  AND EXISTS (
                    SELECT 1 FROM cs_conversation_events ev
                    WHERE ev.session_id = e.session_id
                      AND ev.event_type='session_handoff'
                      AND json_extract(ev.payload_json, '$.phase')='processing'
                      AND ev.created_at < e.created_at
                  )""",
            (env, since, until, *filter_params),
        ).fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["d"], set()).add(str(r["qid"]))
    return out




def _latest_autopilot_job(conn: sqlite3.Connection, session_id: int, env: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM cs_autopilot_jobs WHERE session_id=? AND env=? ORDER BY id DESC LIMIT 1",
        (session_id, env),
    ).fetchone()
    return dict(row) if row else None


def attach_latest_autopilot_jobs(rows: list[dict[str, Any]], *, env: str) -> list[dict[str, Any]]:
    """Attach each session's latest autopilot job as ``__ap_job`` (single query).

    The list endpoint returns bare ``cs_session`` rows which cannot tell
    ``draft_ready`` apart from ``autopilot``. The frontend ``mapRow`` reads
    ``row.__ap_job`` to apply the same split as ``session_display_counts``
    (latest job ``status='scheduled'`` => autopilot), so chip counts and the
    filtered list stay consistent.
    """
    if not rows:
        return rows
    ids = [r["id"] for r in rows if r.get("id") is not None]
    if not ids:
        return rows
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        job_rows = conn.execute(
            f"SELECT * FROM cs_autopilot_jobs WHERE env=? AND session_id IN ({placeholders})",
            [env, *ids],
        ).fetchall()
    latest: dict[int, dict[str, Any]] = {}
    for j in job_rows:
        sid = j["session_id"]
        if sid not in latest or j["id"] > latest[sid]["id"]:
            latest[sid] = dict(j)
    for r in rows:
        r["__ap_job"] = latest.get(r["id"])
    return rows


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
    # Unchanged-check must consider attachments too: an operator uploading a
    # file without touching the draft body still needs the new attachment
    # persisted. Compare normalized JSON of incoming vs stored draft_attachments.
    incoming_atts_json = (
        json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
        if attachments is not None
        else None
    )
    sess_atts_raw = sess.get("draft_attachments") or "[]"
    atts_unchanged = incoming_atts_json is None or incoming_atts_json == sess_atts_raw
    if (
        (sess.get("draft_html") or "") == draft_html
        and (sess.get("draft_source") or "") == source
        and atts_unchanged
    ):
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


def session_has_event_since(
    *,
    session_row_id: int,
    event_type: str,
    since: str,
) -> bool:
    """True when an event of ``event_type`` exists with ``created_at >= since``.

    Used by orphaned-escalation repair so a prior-cycle ``operator_sent`` does not
    close a newer open escalation after customer reopen.
    """
    since_ts = (since or "").strip()
    if not since_ts:
        return False
    with _connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM cs_conversation_events
               WHERE session_id=? AND event_type=? AND created_at>=?
               LIMIT 1""",
            (session_row_id, event_type, since_ts),
        ).fetchone()
        return row is not None


def latest_event_created_at(
    *,
    session_row_id: int,
    event_types: tuple[str, ...] | list[str],
    ok_only: bool = False,
) -> Optional[str]:
    """Return the newest ``created_at`` among the given event types, or None.

    When ``ok_only=True``, restrict to events whose ``payload_json->>'ok'`` is
    ``'true'``. Used by the leave-on-terminal idempotency guard so a prior
    *failed* leave (``ok:false``) does NOT count as "already left" — the
    session may still be joined in QuickCEP and a manual retry must be allowed.
    """
    types = tuple(t for t in event_types if str(t).strip())
    if not types:
        return None
    placeholders = ",".join("?" for _ in types)
    # SQLite json_extract returns JSON true/false as 1/0 (not 'true'/'false'),
    # and NULL for missing keys. `IS 1` matches only real boolean true and
    # treats missing/NULL/false as non-matching (excluded from "already left").
    ok_clause = " AND json_extract(payload_json, '$.ok') IS 1" if ok_only else ""
    with _connect() as conn:
        row = conn.execute(
            f"""SELECT MAX(created_at) AS ts FROM cs_conversation_events
                WHERE session_id=? AND event_type IN ({placeholders}){ok_clause}""",
            (session_row_id, *types),
        ).fetchone()
        if not row or row["ts"] is None:
            return None
        return str(row["ts"])


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


def update_last_message_id(
    *,
    quickcep_session_id: str,
    message_id: str,
    env: str = "LIVE",
) -> bool:
    """Bump ``last_message_id`` only — status-preserving consume helper.

    Used by the customer-rating message consume gate. Unlike
    ``enqueue_session``, this NEVER:

    - inserts a ``cs_message_dedup`` row (caller decides dedup separately)
    - changes ``status`` (terminal/in-flight statuses stay as-is)
    - stamps ``processing_started_at`` / ``agent_processing_at``

    ``updated_at`` is intentionally NOT bumped so the re-arm scanner's
    age filter (``_REARM_MAX_AGE_HOURS``) is not reset by a CSAT event —
    otherwise a closed session that received a rating would be re-scanned
    every 5 minutes for 168 hours. The bump is observable via
    ``last_message_id`` itself plus the ``inbound_skipped`` audit event.

    Returns True when a session row was found and updated.
    """
    if not quickcep_session_id or not message_id:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE cs_session SET last_message_id=? "
            "WHERE quickcep_session_id=? AND env=?",
            (str(message_id), quickcep_session_id, env),
        )
        conn.commit()
        return cur.rowcount > 0


# Cross-namespace dedup window: SIO and REST re-deliver the SAME rating within
# ~1-2 minutes (SIO immediate, REST reconcile ~60s poll). A DISTINCT second
# rating (session reopened → re-resolved → re-rated) arrives hours later. The
# dedup suppresses the duplicate audit only within this window so a later
# distinct rating still gets its own audit event (funnel accuracy).
_RATING_DEDUP_WINDOW_MINUTES = 5


def consume_rating_atomic(
    *,
    quickcep_session_id: str,
    message_id: str,
    content_type: str = "",
    env: str = "LIVE",
) -> str:
    """Atomic rating consume: idempotent bump + audit in one transaction.

    Single-transaction consume of a customer rating / CSAT message. Wraps the
    session lookup, cross-namespace dedup check, ``last_message_id`` bump,
    and ``inbound_skipped`` audit insert in one ``BEGIN IMMEDIATE`` transaction
    so concurrent SIO + REST delivery of the same rating cannot write
    duplicate audit events or race on ``last_message_id``.

    Unlike ``enqueue_session``, this NEVER:

    - inserts a ``cs_message_dedup`` row
    - changes ``status`` (terminal/in-flight statuses stay as-is)
    - stamps ``processing_started_at`` / ``agent_processing_at``
    - bumps ``updated_at`` (re-arm scanner age filter stays intact)

    Cross-namespace dedup: SIO delivers a rating with a native ``id`` (e.g.
    ``"abc123"``) while REST reconcile builds ``rest:{lastMsgTime}``. Exact-
    string idempotency on ``last_message_id`` would let the second transport
    re-consume and write a duplicate audit event. This function detects a
    prior ``inbound_skipped`` event with ``gate=customer_rating`` for the
    session **within a short dedup window** (``_RATING_DEDUP_WINDOW_MINUTES``)
    and, if present, bumps ``last_message_id`` (so REST stops re-polling) but
    does NOT write a new audit event. The window is bounded so a DISTINCT
    second rating (session reopened → re-resolved → re-rated, hours later)
    still gets its own audit event instead of being suppressed.

    Returns one of:

    - ``"consumed"``: first consume for this session (audit written + bump)
    - ``"idempotent"``: ``last_message_id`` already equals ``message_id``
      (same-namespace redelivery — no audit, no bump)
    - ``"cross_namespace_dedup"``: a prior rating audit exists for this
      session; ``last_message_id`` is bumped to stop the REST loop but no
      new audit is written
    - ``"missing"``: no CAL session row (rating on a never-AI-processed
      session — no action)
    - ``"invalid"``: empty ``quickcep_session_id`` or ``message_id``
    """
    if not quickcep_session_id or not message_id:
        return "invalid"
    with _connect() as conn:
        # BEGIN IMMEDIATE acquires the write lock up front so two concurrent
        # consumers (SIO + REST) serialize: the second blocks until the first
        # commits, then sees the committed last_message_id / audit row.
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id, status, last_message_id FROM cs_session "
                "WHERE quickcep_session_id=? AND env=?",
                (quickcep_session_id, env),
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                log.info(
                    "cs.intake session=%s env=%s message_id=%s "
                    "decision=rating_consume_missing content_type=%s "
                    "(no CAL session row)",
                    quickcep_session_id, env, message_id, content_type or "-",
                )
                return "missing"
            sess_id = row["id"]
            status = str(row["status"] or "")
            last_msg = str(row["last_message_id"] or "")
            if last_msg == str(message_id):
                conn.execute("ROLLBACK")
                log.info(
                    "cs.intake session=%s env=%s message_id=%s "
                    "decision=rating_consume_idempotent (same-namespace redelivery)",
                    quickcep_session_id, env, message_id,
                )
                return "idempotent"
            prior = conn.execute(
                "SELECT 1 FROM cs_conversation_events "
                "WHERE session_id=? AND event_type='inbound_skipped' "
                "AND json_extract(payload_json, '$.gate')='customer_rating' "
                "AND datetime(created_at) >= datetime('now', ?) "
                "LIMIT 1",
                (sess_id, f"-{_RATING_DEDUP_WINDOW_MINUTES} minutes"),
            ).fetchone()
            conn.execute(
                "UPDATE cs_session SET last_message_id=? WHERE id=?",
                (str(message_id), sess_id),
            )
            if not prior:
                payload = {
                    "gate": "customer_rating",
                    "message_id": str(message_id),
                    "contentType": str(content_type or ""),
                    "prior_status": status,
                    "status": status,
                }
                conn.execute(
                    """INSERT INTO cs_conversation_events
                       (session_id, event_type, payload_json, env, created_at)
                       VALUES (?,?,?,?,?)""",
                    (
                        sess_id,
                        "inbound_skipped",
                        json.dumps(sanitize_mapping(payload)),
                        env,
                        _now(),
                    ),
                )
            conn.execute("COMMIT")
            outcome = "cross_namespace_dedup" if prior else "consumed"
            log.info(
                "cs.intake session=%s env=%s message_id=%s "
                "decision=rating_consume_%s content_type=%s status=%s "
                "audit_written=%s",
                quickcep_session_id, env, message_id, outcome,
                content_type or "-", status, "no" if prior else "yes",
            )
            return outcome
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise


def update_session_status(
    *,
    session_row_id: int,
    status: str,
    allow_regression: bool = False,
) -> None:
    """Update the session status. Stamps ``processing_started_at`` on first activation.

    By default forbids status regressions (target rank < current rank) to catch
    bugs where a caller bypasses ``session_handoff.apply_handoff`` and writes a
    lower status directly. Callers that intentionally reset a session backward
    (e.g. the watcher rolling back ``processing`` → ``pending`` on a transient
    gateway failure, or ``processing_stale`` marking ``processing`` → ``failed``)
    must pass ``allow_regression=True``. A blocked regression logs a WARNING and
    leaves the status unchanged — it does NOT raise, so a fail-soft caller is
    not disrupted.
    """
    if status not in SESSION_STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = _now()
    with _connect() as conn:
        if not allow_regression:
            cur = conn.execute(
                "SELECT status FROM cs_session WHERE id=?", (session_row_id,)
            ).fetchone()
            if cur:
                cur_status = str(cur[0])
                cur_rank = _STATUS_RANKS.get(cur_status, 0)
                target_rank = _STATUS_RANKS.get(status, 0)
                if target_rank < cur_rank:
                    log.warning(
                        "cs.state.transition BLOCKED session_row_id=%s %s->%s "
                        "reason=regression_blocked allow_regression=false "
                        "(pass allow_regression=True to override)",
                        session_row_id, cur_status, status,
                    )
                    conn.commit()  # no-op commit; preserve
                    return
                # Audit: log every successful status transition. This is the
                # single chokepoint for all CAL status changes (apply_handoff,
                # close_session, watcher, processing_stale, relaunch all flow
                # through here), so logging here gives complete post-hoc audit
                # of the state machine without instrumenting every caller.
                if cur_status != status:
                    log.info(
                        "cs.state.transition session_row_id=%s %s->%s "
                        "allow_regression=%s",
                        session_row_id, cur_status, status, allow_regression,
                    )
        else:
            # allow_regression=True path: still audit the transition (caller
            # explicitly authorized a backward/reset move — record it).
            cur = conn.execute(
                "SELECT status FROM cs_session WHERE id=?", (session_row_id,)
            ).fetchone()
            cur_status = str(cur[0]) if cur else "?"
            if cur_status != status:
                log.info(
                    "cs.state.transition session_row_id=%s %s->%s "
                    "allow_regression=true",
                    session_row_id, cur_status, status,
                )
        # v5: stamp processing_started_at the first time a session leaves
        # `pending` for any active state. COALESCE keeps the original value
        # on subsequent transitions so the daily report can bucket by the
        # first-active day rather than by the volatile `updated_at`.
        if status != "pending":
            conn.execute(
                "UPDATE cs_session SET status=?, "
                "processing_started_at=COALESCE(processing_started_at, ?), "
                "updated_at=? WHERE id=?",
                (status, now, now, session_row_id),
            )
        else:
            conn.execute(
                "UPDATE cs_session SET status=?, updated_at=? WHERE id=?",
                (status, now, session_row_id),
            )
        conn.commit()


def stamp_agent_processing_at(*, session_row_id: int, reset: bool = False) -> None:
    """Stamp ``agent_processing_at`` once, when the agent first confirms processing.

    Distinct from ``processing_started_at`` (which the watcher stamps when it
    sets status=processing). The gap between the two = agent startup + first
    model response latency. Idempotent: COALESCE keeps the earliest value so
    repeat ``apply-handoff --phase processing`` calls do not overwrite.

    ``reset=True`` clears the anchor (sets it to NULL) — used when a session
    re-enters ``processing`` via reopen or manual relaunch. A stale anchor from
    a prior cycle would suppress the never-confirmed heartbeat in
    processing_stale (which fires when ``agent_processing_at`` IS NULL), so the
    new cycle must re-confirm or be recovered fast.
    """
    now = _now()
    with _connect() as conn:
        if reset:
            conn.execute(
                "UPDATE cs_session SET agent_processing_at=NULL, updated_at=? WHERE id=?",
                (now, session_row_id),
            )
        else:
            conn.execute(
                "UPDATE cs_session SET agent_processing_at=COALESCE(agent_processing_at, ?), "
                "updated_at=? WHERE id=?",
                (now, now, session_row_id),
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
        log.warning(
            "cs.event.write SKIPPED session=%s env=%s event_type=%s "
            "reason=session_not_found",
            quickcep_session_id, env, event_type,
        )
        return False
    safe_payload = sanitize_mapping(dict(payload or {}))
    with _connect() as conn:
        conn.execute(
            """INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at)
               VALUES (?,?,?,?,?)""",
            (sess["id"], event_type, json.dumps(safe_payload), env, _now()),
        )
        conn.commit()
    # Audit: every CAL event write is logged so the application log mirrors the
    # cs_conversation_events table — operators can reconstruct the session
    # timeline from logs alone (post-hoc audit / troubleshooting). The
    # payload `source` field (when present) distinguishes who/what drove the
    # event (failed_handoff / skipped_handoff / console_close / console_leave_only
    # / intent_gate_skip / agent_launch / operator / ...).
    src = safe_payload.get("source") if isinstance(safe_payload, dict) else None
    log.info(
        "cs.event.write session=%s env=%s session_row_id=%s event_type=%s source=%s",
        quickcep_session_id, env, sess["id"], event_type, src or "-",
    )
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

    # Dedup: if an escalation is already open for this session, return it instead
    # of creating a duplicate. Prevents retry storms (e.g. client timeout + retry).
    existing = list_escalations_for_session(
        quickcep_session_id=quickcep_session_id,
        states=("awaiting_answer", "resuming"),
        env=env,
    )
    if existing:
        log.info(
            "cs.escalation.open session=%s env=%s escalation_id=%s "
            "decision=deduped reason=%s urgency=%s",
            quickcep_session_id, env, existing[0]["id"], reason, urgency,
        )
        return existing[0]["id"]

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
        esc_id = int(cur.lastrowid)
        log.info(
            "cs.escalation.open session=%s env=%s escalation_id=%s "
            "decision=created reason=%s urgency=%s feishu_chat_id=%s",
            quickcep_session_id, env, esc_id, reason, urgency, feishu_chat_id,
        )
        return esc_id


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


def get_escalation_feishu_ids(*, escalation_id: int) -> Optional[dict[str, Optional[str]]]:
    """Return only the persisted Feishu delivery ids for an escalation.

    Used by `POST /escalations` to detect idempotent retries: when an escalation
    was deduped and already has a delivered Feishu message, the route skips the
    re-send (which would post a duplicate group message) and returns the existing
    thread info so the agent can still apply-handoff. Lighter than
    `get_escalation` (no session join) since this runs on every open-escalation.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT feishu_chat_id, feishu_thread_id, feishu_message_id "
            "FROM cs_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "feishu_chat_id": row["feishu_chat_id"],
            "feishu_thread_id": row["feishu_thread_id"],
            "feishu_message_id": row["feishu_message_id"],
        }


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


def get_latest_escalation_with_operator_answer(
    *,
    quickcep_session_id: str,
    env: str = "LIVE",
) -> Optional[dict[str, Any]]:
    """Return the latest escalation with a recorded expert answer for a session.

    Prefers a ``resuming`` row (stuck retry candidate) over any other state.
    Falls back to the most recent escalation that has ``operator_answer_raw``
    in its resume_context (covers ``resolved`` rows from prior failures).
    """
    resuming = get_resuming_escalation_for_session(
        quickcep_session_id=quickcep_session_id, env=env,
    )
    if resuming:
        return resuming
    sess = get_session(quickcep_session_id=quickcep_session_id, env=env)
    if not sess:
        return None
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM cs_escalations
               WHERE session_id=? AND env=?
                 AND json_extract(resume_context_json, '$.operator_answer_raw') != ''
               ORDER BY CASE WHEN state='resuming' THEN 0 ELSE 1 END, id DESC
               LIMIT 1""",
            (sess["id"], env),
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["resume_context"] = json.loads(out.pop("resume_context_json") or "{}")
        out["session"] = dict(sess)
        return out


def reopen_escalation_for_resume(*, escalation_id: int) -> bool:
    """Atomically reset an escalation to ``resuming`` for manual retry.

    Clears resume run / failure / Feishu-done markers from resume_context
    while preserving ``operator_answer_raw`` and attachments. Resets
    ``resume_launched_at`` to now so the 4h resuming-timeout anchor restarts
    (otherwise the timeout checker would immediately re-close the escalation).
    Sets ``retried_at`` so the Feishu DONE message can show a retry tag.
    """
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_context_json FROM cs_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
        if not row:
            log.info(
                "cs.escalation.reopen escalation_id=%s decision=rejected reason=not_found",
                escalation_id,
            )
            return False
        ctx = json.loads(row["resume_context_json"] or "{}")
        for key in (
            "resume_run_id",
            "resume_launched_at",
            "feishu_done_notified",
            "feishu_done_message_id",
            "resuming_timeout_handled",
            "resume_failed_detected",
            "resume_failed_notified",
            "resume_fail_notified_at",
            "resume_fail_reason",
        ):
            ctx.pop(key, None)
        ctx["resume_launched_at"] = now
        ctx["retried_at"] = now
        cur = conn.execute(
            """UPDATE cs_escalations SET
                   state='resuming',
                   decision='claim',
                   resume_context_json=?,
                   updated_at=?
               WHERE id=?""",
            (json.dumps(ctx), now, escalation_id),
        )
        conn.commit()
        log.info(
            "cs.escalation.reopen escalation_id=%s decision=reopened "
            "to_state=resuming retried_at=%s outcome=%s",
            escalation_id, now,
            "applied" if cur.rowcount == 1 else "rejected_not_found",
        )
        return cur.rowcount == 1


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
        log.info(
            "cs.escalation.claim escalation_id=%s decision=rejected reason=empty_answer "
            "decided_by=%s feishu_reply_message_id=%s",
            escalation_id, decided_by, feishu_reply_message_id,
        )
        return False
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT resume_context_json FROM cs_escalations WHERE id=? AND state='awaiting_answer'",
            (escalation_id,),
        ).fetchone()
        if not row:
            log.info(
                "cs.escalation.claim escalation_id=%s decision=rejected "
                "reason=not_found_or_not_awaiting_answer decided_by=%s "
                "feishu_reply_message_id=%s",
                escalation_id, decided_by, feishu_reply_message_id,
            )
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
        log.info(
            "cs.escalation.claim escalation_id=%s from_state=awaiting_answer "
            "to_state=resuming decision=%s decided_by=%s feishu_reply_message_id=%s",
            escalation_id,
            "claimed" if cur.rowcount == 1 else "rejected_state_changed",
            decided_by, feishu_reply_message_id,
        )
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
        log.info(
            "cs.escalation.finalize escalation_id=%s from_state=resuming "
            "to_state=%s decision=%s outcome=%s",
            escalation_id, final_state, decision,
            "applied" if cur.rowcount == 1 else "rejected_not_resuming",
        )
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
            log.info(
                "cs.escalation.resolve escalation_id=%s decision=rejected reason=not_found",
                escalation_id,
            )
            return False
        conn.execute(
            """UPDATE cs_escalations SET
                   decision=?, decided_by=?, decided_at=?, operator_answer=COALESCE(?, operator_answer),
                   state=?, updated_at=?
               WHERE id=?""",
            (decision, decided_by, now, masked_answer, final_state, now, escalation_id),
        )
        session_status_changed = "none"
        if final_state == "resolved" and touch_session:
            sess_row = conn.execute(
                "SELECT status FROM cs_session WHERE id=?", (row["session_id"],)
            ).fetchone()
            prior_status = str(sess_row[0]) if sess_row else "?"
            conn.execute(
                "UPDATE cs_session SET status='processing', updated_at=? WHERE id=?",
                (now, row["session_id"]),
            )
            session_status_changed = "processing"
            # Audit the session status change here (rather than via
            # update_session_status) to preserve the single-transaction atomicity
            # of the escalation+session resolve. Mirrors the cs.state.transition
            # format so dashboards/grep stay consistent.
            if prior_status != "processing":
                log.info(
                    "cs.state.transition session_row_id=%s %s->%s "
                    "allow_regression=true source=resolve_escalation escalation_id=%s",
                    row["session_id"], prior_status, "processing", escalation_id,
                )
        conn.commit()
        log.info(
            "cs.escalation.resolve escalation_id=%s decision=%s decided_by=%s "
            "final_state=%s touch_session=%s session_row_id=%s "
            "session_status_changed=%s",
            escalation_id, decision, decided_by, final_state, touch_session,
            row["session_id"], session_status_changed,
        )
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
        log.info(
            "cs.escalation.patch_decision escalation_id=%s decision=%s "
            "decided_by=%s operator_answer_provided=%s outcome=%s",
            escalation_id, decision, decided_by, bool(operator_answer),
            "applied" if cur.rowcount == 1 else "rejected_not_found",
        )
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


# ─── Global pause flag (下班 → 全局暂停 AI 处理) ─────────────────────
# Stored in cs_poller_state under a reserved key so no schema migration needed.
_GLOBAL_PAUSE_KEY = "_global_pause"


def is_globally_paused() -> bool:
    """True when the system is globally paused (no new AI launches).

    Set by Console「下班」→ bridge ``/admin/pause``. The watcher checks this
    before launching any new inbound run; in-flight runs complete naturally.
    """
    state = get_poller_state(_GLOBAL_PAUSE_KEY)
    return bool(state.get("paused"))


def set_global_pause(*, paused: bool, by: str = "") -> None:
    """Set or clear the global pause flag."""
    set_poller_state(_GLOBAL_PAUSE_KEY, {
        "paused": bool(paused),
        "by": by,
        "at": datetime.now(timezone.utc).isoformat(),
    })


# ─── Test/invalid session purge ──────────────────────────────────────
# QuickCEP chatSubSessionId is a 19-digit numeric Long. Rows whose
# quickcep_session_id is non-numeric (e.g. "sess-1", "qs", "x") are test
# fixtures that leaked into the LIVE CAL DB and pollute the reconcile loop
# (operator_send_reconcile) — every tick they trigger a failing QuickCEP API
# call (JSON parse error: Cannot deserialize Long from "sess-1"). These
# helpers find and remove them deterministically, with a pre-backup.

# Valid QuickCEP sub-session id: 15-20 digits (snowflake-ish). Conservative.
_VALID_QSID = re.compile(r"^\d{15,20}$")


def is_valid_quickcep_session_id(qsid: str | None) -> bool:
    """True when ``qsid`` looks like a real QuickCEP sub-session id."""
    if not qsid:
        return False
    return bool(_VALID_QSID.match(str(qsid).strip()))


def list_test_sessions(*, env: str | None = None) -> list[dict[str, Any]]:
    """Return cs_session rows whose quickcep_session_id is NOT a valid numeric id.

    These are test/placeholder rows (e.g. ``sess-1``, ``qs``, ``x``) that leak
    into the CAL DB and break the operator_send_reconcile loop. ``env`` None
    scans all envs; pass ``"LIVE"`` to scope.
    """
    with _connect() as conn:
        if env:
            rows = conn.execute(
                "SELECT * FROM cs_session WHERE env=? ORDER BY id",
                (env,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cs_session ORDER BY id").fetchall()
        return [dict(r) for r in rows if not is_valid_quickcep_session_id(r["quickcep_session_id"])]


# Child tables that reference cs_session(id). FK enforcement is OFF in the
# running bridge (_connect only sets journal_mode), so purge must delete each
# child explicitly. Order matters for vault_blob ref_count accounting.
_SESSION_CHILD_TABLES = (
    "cs_conversation_events",
    "cs_facts",
    "cs_autopilot_jobs",
)


def purge_sessions_by_ids(
    *,
    row_ids: list[int],
    env: str = "LIVE",
    dry_run: bool = True,
    backup: bool = True,
) -> dict[str, Any]:
    """Delete cs_session rows (and their children) by primary key.

    Deterministic, idempotent cleanup used to evict test/invalid sessions that
    leak into the CAL DB. Always prefers a pre-backup; set ``backup=False`` only
    when the caller has already snapshotted the DB.

    Args:
        row_ids: cs_session.id values to delete.
        env: env filter — only rows matching both id AND env are removed.
        dry_run: when True, no writes happen; the return describes what would
            be deleted (counts + sampled ids).
        backup: when True (and not dry_run), copy the DB file to
            ``<db>.bak-<utc-iso>`` before any DELETE.

    Returns:
        dict with ``mode`` ("dry_run"|"applied"), ``backup_path``,
        ``deleted`` per-table counts, and ``row_ids`` actually targeted.
    """
    row_ids = [int(i) for i in row_ids]
    if not row_ids:
        return {"mode": "dry_run" if dry_run else "applied", "deleted": {}, "row_ids": []}

    placeholders = ",".join("?" for _ in row_ids)
    result: dict[str, Any] = {"row_ids": row_ids, "deleted": {}}

    with _connect() as conn:
        # Snapshot the target rows + their escalation ids (for vault link cleanup).
        targets = conn.execute(
            f"SELECT id, quickcep_session_id FROM cs_session WHERE id IN ({placeholders}) AND env=?",
            (*row_ids, env),
        ).fetchall()
        target_ids = [r["id"] for r in targets]
        target_qsids = [r["quickcep_session_id"] for r in targets]
        result["matched"] = [
            {"id": i, "quickcep_session_id": q} for i, q in zip(target_ids, target_qsids)
        ]
        if not target_ids:
            result["mode"] = "dry_run" if dry_run else "applied"
            result["deleted"] = {}
            return result

        esc_rows = conn.execute(
            f"SELECT id FROM cs_escalations WHERE session_id IN ({','.join('?' for _ in target_ids)})",
            tuple(target_ids),
        ).fetchall()
        esc_ids = [r["id"] for r in esc_rows]

        # Pre-count what would be removed (also reported on apply for verification).
        counts: dict[str, int] = {
            "cs_session": len(target_ids),
            "cs_conversation_events": conn.execute(
                f"SELECT COUNT(*) AS n FROM cs_conversation_events WHERE session_id IN ({','.join('?' for _ in target_ids)})",
                tuple(target_ids),
            ).fetchone()["n"],
            "cs_facts": conn.execute(
                f"SELECT COUNT(*) AS n FROM cs_facts WHERE session_id IN ({','.join('?' for _ in target_ids)})",
                tuple(target_ids),
            ).fetchone()["n"],
            "cs_autopilot_jobs": conn.execute(
                f"SELECT COUNT(*) AS n FROM cs_autopilot_jobs WHERE session_id IN ({','.join('?' for _ in target_ids)})",
                tuple(target_ids),
            ).fetchone()["n"],
            "cs_escalations": len(esc_ids),
        }
        if esc_ids:
            counts["escalation_vault_link"] = conn.execute(
                f"SELECT COUNT(*) AS n FROM escalation_vault_link WHERE escalation_id IN ({','.join('?' for _ in esc_ids)})",
                tuple(esc_ids),
            ).fetchone()["n"]
        # cs_message_dedup is keyed by quickcep_session_id (not session_id).
        if target_qsids:
            counts["cs_message_dedup"] = conn.execute(
                f"SELECT COUNT(*) AS n FROM cs_message_dedup WHERE quickcep_session_id IN ({','.join('?' for _ in target_qsids)})",
                tuple(target_qsids),
            ).fetchone()["n"]
        result["deleted"] = counts

        if dry_run:
            result["mode"] = "dry_run"
            return result

        # Apply: backup first.
        if backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            bak = _DB_PATH.with_suffix(f".db.bak-{ts}")
            shutil.copy2(_DB_PATH, bak)
            result["backup_path"] = str(bak)

        # Vault links + blob ref_count decrement (mirror delete_vault_link).
        if esc_ids:
            link_rows = conn.execute(
                f"SELECT id, blob_md5 FROM escalation_vault_link WHERE escalation_id IN ({','.join('?' for _ in esc_ids)})",
                tuple(esc_ids),
            ).fetchall()
            for lr in link_rows:
                conn.execute("DELETE FROM escalation_vault_link WHERE id=?", (lr["id"],))
                conn.execute(
                    "UPDATE vault_blob SET ref_count = CASE WHEN ref_count > 0 THEN ref_count - 1 ELSE 0 END WHERE md5=?",
                    (lr["blob_md5"],),
                )

        for tbl in _SESSION_CHILD_TABLES:
            conn.execute(
                f"DELETE FROM {tbl} WHERE session_id IN ({','.join('?' for _ in target_ids)})",
                tuple(target_ids),
            )
        if esc_ids:
            conn.execute(
                f"DELETE FROM cs_escalations WHERE id IN ({','.join('?' for _ in esc_ids)})",
                tuple(esc_ids),
            )
        if target_qsids:
            conn.execute(
                f"DELETE FROM cs_message_dedup WHERE quickcep_session_id IN ({','.join('?' for _ in target_qsids)})",
                tuple(target_qsids),
            )
        conn.execute(
            f"DELETE FROM cs_session WHERE id IN ({','.join('?' for _ in target_ids)}) AND env=?",
            (*target_ids, env),
        )
        conn.commit()
        result["mode"] = "applied"
        return result
