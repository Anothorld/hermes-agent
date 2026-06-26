"""SQLite CAL for cs-ops-bridge."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

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
                   status=CASE WHEN status IN ('draft_ready','operator_replied','skipped','failed','reviewed') THEN 'pending'
                            ELSE status END,
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
        result = {
            "created": True,
            "deduped": False,
            "should_launch": should_launch,
            "session": session,
        }
        return result


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


def session_has_event(*, session_row_id: int, event_type: str) -> bool:
    """True when at least one CAL conversation event of ``event_type`` exists."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM cs_conversation_events WHERE session_id=? AND event_type=? LIMIT 1",
            (session_row_id, event_type),
        ).fetchone()
        return row is not None


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


def _fetch_visitor_orders(quickcep_session_id: str) -> dict[str, Any]:
    """Fetch customer orders from QuickCEP getOrderList API.

    Uses the same quickcep_cli import pattern as email_channel.fetch_email_session_row.
    Returns {"orders": [...], "userUUID": str|None, "customer_email": str|None, "source": str}.
    Best-effort: on any failure returns empty orders list.
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
