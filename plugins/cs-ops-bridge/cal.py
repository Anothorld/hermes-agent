"""SQLite CAL for cs-ops-bridge."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .schema import ESCALATION_STATES, SESSION_STATUSES, recreate_all
from .pii_sanitize import mask_string, sanitize_mapping, sanitize_namespaces

_DB_PATH = Path(
    os.environ.get(
        "HERMES_CS_OPS_CAL_DB",
        Path(os.path.expanduser("~/.hermes/cs-ops-bridge/cal.db")),
    )
)


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
) -> dict[str, Any]:
    """Idempotent enqueue; returns ``created`` flag and session row."""
    dedup_key = f"{env}:{quickcep_session_id}:{message_id}"
    now = _now()
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
                   last_message_id, status, env, created_at, updated_at
               ) VALUES (?,?,?,?, 'pending', ?, ?, ?)
               ON CONFLICT(quickcep_session_id, env) DO UPDATE SET
                   chat_session_id=COALESCE(excluded.chat_session_id, chat_session_id),
                   customer_email=COALESCE(excluded.customer_email, customer_email),
                   last_message_id=excluded.last_message_id,
                   status=CASE WHEN cs_session.status IN ('draft_ready','skipped','failed','reviewed') THEN 'pending'
                            ELSE cs_session.status END,
                   updated_at=excluded.updated_at
            """,
            (quickcep_session_id, chat_session_id, customer_email, message_id, env, now, now),
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
        return {
            "created": True,
            "deduped": False,
            "should_launch": should_launch,
            "session": session,
        }


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


def update_session_status(*, session_row_id: int, status: str) -> None:
    if status not in SESSION_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with _connect() as conn:
        conn.execute(
            "UPDATE cs_session SET status=?, updated_at=? WHERE id=?",
            (status, _now(), session_row_id),
        )
        conn.commit()


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
        conn.execute(
            "UPDATE cs_session SET status='awaiting_expert', updated_at=? WHERE id=?",
            (now, sess["id"]),
        )
        conn.commit()
        return int(cur.lastrowid)


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


def resolve_escalation(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
    final_state: str = "resolved",
) -> bool:
    if final_state not in ESCALATION_STATES:
        raise ValueError(f"invalid final_state: {final_state}")
    now = _now()
    with _connect() as conn:
        row = conn.execute("SELECT session_id FROM cs_escalations WHERE id=?", (escalation_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE cs_escalations SET
                   decision=?, decided_by=?, decided_at=?, operator_answer=COALESCE(?, operator_answer),
                   state=?, updated_at=?
               WHERE id=?""",
            (decision, decided_by, now, operator_answer, final_state, now, escalation_id),
        )
        if final_state == "resolved":
            conn.execute(
                "UPDATE cs_session SET status='processing', updated_at=? WHERE id=?",
                (now, row["session_id"]),
            )
        conn.commit()
    return True


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
