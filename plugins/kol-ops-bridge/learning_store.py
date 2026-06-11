"""Read helpers for KOL learning loops (events, facts, policies).

Pure DB reads via an open sqlite3 connection or cal wrappers — no HTTP.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
from collections import defaultdict
from typing import Any, Final, Optional

from .gmail_client import is_bounce_body

LEARNING_EVENT_TYPES = (
    "draft_rejected_learning",
    "draft_edit_learning",
)

REJECT_LEARNING_SCOPE = "reply_learning"
REPLY_STRATEGY_SCOPE = "reply_strategy"
OUTCOME_STRATEGY_SCOPE = "outcome_strategy"
EDIT_LEARNING_SCOPES = ("company_style", "user_style")
STYLE_LEARNING_APPROVAL_FACT = "approval.style_learning_proposal"
PRICING_CALIBRATION_SCOPE = "pricing_calibration"

_GOAL_SECTION = re.compile(r"^##\s+(.+?)\s*$")

# Conversation types included in style-learning LLM context (newest-first query).
_STYLE_TIMELINE_EVENT_TYPES: Final[tuple[str, ...]] = (
    "kol_inbound_reply",
    "outbound_sent",
    "kol_reply_draft_ready",
    "outbound_draft_created",
    "draft_edit_learning",
)

_FACT_PREFIX_PRIORITY: Final[tuple[str, ...]] = (
    "offer.",
    "approval.",
    "identity.",
    "fulfillment.",
    "payout.",
)

def is_bounce_edit_learning_payload(payload: dict[str, Any]) -> bool:
    """True when stored sent body is a Gmail delivery-failure DSN, not operator text."""
    sent = str(
        payload.get("normalized_sent_body") or payload.get("sent_body") or "",
    )
    return bool(sent.strip()) and is_bounce_body(sent)


def is_bounce_edit_learning_event(ev: dict[str, Any]) -> bool:
    """True for ``draft_edit_learning`` rows whose sent body is a bounce DSN."""
    if ev.get("event_type") != "draft_edit_learning":
        return False
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return is_bounce_edit_learning_payload(payload)


def _edit_learning_pair_key(
    identity_id: Any,
    campaign_id: Any,
) -> tuple[int, str]:
    return (int(identity_id or 0), str(campaign_id or ""))


def delivery_failed_edit_learning_pairs(
    conn: sqlite3.Connection,
    *,
    env: str,
) -> set[tuple[int, str]]:
    """Identity+campaign pairs with a stored bounce DSN edit-learning row.

    A bounce capture means Gmail delivery failed for that outreach; all edit
    samples for the pair are excluded from UI, stats, and distill batches.
    """
    rows = conn.execute(
        """SELECT identity_id, campaign_id, payload_json
             FROM kol_conversation_events
            WHERE env=? AND event_type='draft_edit_learning'""",
        (env,),
    ).fetchall()
    failed: set[tuple[int, str]] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict) or not is_bounce_edit_learning_payload(payload):
            continue
        failed.add(_edit_learning_pair_key(row["identity_id"], row["campaign_id"]))
    return failed


def exclude_delivery_failed_edit_events(
    events: list[dict[str, Any]],
    failed_pairs: set[tuple[int, str]],
) -> list[dict[str, Any]]:
    """Drop edit-learning rows for identity+campaign pairs with delivery failure."""
    if not failed_pairs:
        return events
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event_type") != "draft_edit_learning":
            out.append(ev)
            continue
        pair = _edit_learning_pair_key(ev.get("identity_id"), ev.get("campaign_id"))
        if pair in failed_pairs:
            continue
        out.append(ev)
    return out


def style_learning_batch_size() -> int:
    """Min ``draft_edit_learning`` (was_edited) events before LLM distill runs."""
    raw = os.environ.get("KOL_STYLE_LEARNING_BATCH_SIZE", "5").strip()
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        return 5


def _env_flag(name: str, *, default: bool) -> bool:
    """Read a boolean env toggle (``1/true/yes/on`` → True)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _decode_fact_value(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _truncate_text(val: Any, *, max_len: int = 600) -> str:
    text = str(val or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def latest_facts_snapshot(
    conn: sqlite3.Connection,
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    max_keys: int = 80,
) -> dict[str, Any]:
    """Latest CAL facts for (identity, campaign) — identity-level merged under campaign."""
    ident_rows = conn.execute(
        """SELECT fact_key, fact_value FROM kol_facts_latest
            WHERE identity_id=? AND campaign_id IS NULL AND env=?""",
        (int(identity_id), env),
    ).fetchall()
    camp_rows: list[Any] = []
    if campaign_id:
        camp_rows = conn.execute(
            """SELECT fact_key, fact_value FROM kol_facts_latest
                WHERE identity_id=? AND campaign_id=? AND env=?""",
            (int(identity_id), campaign_id, env),
        ).fetchall()
    merged: dict[str, Any] = {
        r["fact_key"]: _decode_fact_value(r["fact_value"]) for r in ident_rows
    }
    for r in camp_rows:
        merged[r["fact_key"]] = _decode_fact_value(r["fact_value"])

    def _priority(key: str) -> tuple[int, str]:
        for idx, prefix in enumerate(_FACT_PREFIX_PRIORITY):
            if key.startswith(prefix):
                return idx, key
        return len(_FACT_PREFIX_PRIORITY), key

    keys = sorted(merged.keys(), key=_priority)[:max_keys]
    out: dict[str, Any] = {}
    for key in keys:
        val = merged[key]
        if isinstance(val, str):
            out[key] = _truncate_text(val, max_len=400)
        elif isinstance(val, (dict, list)):
            try:
                blob = json.dumps(val, ensure_ascii=False)
                out[key] = _truncate_text(blob, max_len=500)
            except (TypeError, ValueError):
                out[key] = val
        else:
            out[key] = val
    return out


def list_conversation_timeline(
    conn: sqlite3.Connection,
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Recent conversation events for style-learning context (newest first)."""
    limit = max(1, min(int(limit), 80))
    placeholders = ",".join("?" * len(_STYLE_TIMELINE_EVENT_TYPES))
    where = [
        "env = ?",
        "identity_id = ?",
        f"event_type IN ({placeholders})",
    ]
    args: list[Any] = [env, int(identity_id), *_STYLE_TIMELINE_EVENT_TYPES]
    if campaign_id:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    sql = (
        "SELECT id, identity_id, campaign_id, event_type, goal, lane, "
        "actor, ts, payload_json, env FROM kol_conversation_events "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    )
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        raw = d.pop("payload_json", None)
        try:
            d["payload"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            d["payload"] = {}
        out.append(d)
    return out


def shape_timeline_event_for_llm(ev: dict[str, Any]) -> dict[str, Any]:
    """Compact one timeline row for LLM consumption."""
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    et = str(ev.get("event_type") or "")
    shaped: dict[str, Any] = {
        "event_id": ev.get("id"),
        "ts": ev.get("ts"),
        "event_type": et,
        "goal": ev.get("goal"),
        "actor": ev.get("actor"),
    }
    if et == "kol_inbound_reply":
        shaped["from"] = payload.get("from_addr") or payload.get("from")
        shaped["subject"] = payload.get("subject")
        shaped["body"] = _truncate_text(
            payload.get("body") or payload.get("snippet") or payload.get("text"),
        )
        shaped["message_id"] = payload.get("message_id")
    elif et in ("outbound_sent", "outbound_draft_created"):
        shaped["thread_id"] = payload.get("thread_id")
        if isinstance(payload.get("gmail_draft"), dict):
            draft = payload["gmail_draft"]
            shaped["draft_subject"] = draft.get("subject")
            shaped["draft_to"] = draft.get("to")
        edit = payload.get("edit_learning")
        if isinstance(edit, dict) and edit.get("was_edited"):
            shaped["operator_edited_before_send"] = True
    elif et == "kol_reply_draft_ready":
        shaped["source_message_id"] = payload.get("source_message_id")
        shaped["child_skill"] = payload.get("child_skill")
    elif et == "draft_edit_learning":
        shaped["child_skill"] = payload.get("child_skill")
        shaped["edit_distance"] = payload.get("edit_distance")
        shaped["agent_excerpt"] = _truncate_text(payload.get("normalized_agent_body"), max_len=280)
        shaped["sent_excerpt"] = _truncate_text(payload.get("normalized_sent_body"), max_len=280)
    return shaped


def build_style_learning_sample(
    conn: sqlite3.Connection,
    ev: dict[str, Any],
    *,
    env: str,
    timeline_limit: int = 30,
) -> dict[str, Any]:
    """Rich context for one edit event: diff + facts + conversation timeline."""
    identity_id = int(ev["identity_id"])
    campaign_id = ev.get("campaign_id")
    cid = str(campaign_id) if campaign_id else None
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}

    timeline = list_conversation_timeline(
        conn,
        identity_id=identity_id,
        campaign_id=cid,
        env=env,
        limit=timeline_limit,
    )
    timeline_chrono = [shape_timeline_event_for_llm(t) for t in reversed(timeline)]

    sent_msg_id = str(payload.get("sent_message_id") or "")
    if sent_msg_id:
        for row in timeline_chrono:
            if row.get("message_id") == sent_msg_id:
                row["is_related_inbound"] = True

    return {
        "event_id": ev.get("id"),
        "identity_id": identity_id,
        "campaign_id": cid,
        "goal": ev.get("goal") or payload.get("goal"),
        "lane": ev.get("lane"),
        "child_skill": payload.get("child_skill"),
        "operator_user_id": payload.get("operator_user_id"),
        "edit_distance": payload.get("edit_distance"),
        "sent_message_id": sent_msg_id or None,
        "current_facts": latest_facts_snapshot(
            conn, identity_id=identity_id, campaign_id=cid, env=env,
        ),
        "conversation_timeline": timeline_chrono,
        "edit": {
            "agent_body": _truncate_text(payload.get("agent_body") or payload.get("normalized_agent_body")),
            "sent_body": _truncate_text(payload.get("sent_body") or payload.get("normalized_sent_body")),
            "normalized_agent_body": _truncate_text(payload.get("normalized_agent_body"), max_len=500),
            "normalized_sent_body": _truncate_text(payload.get("normalized_sent_body"), max_len=500),
        },
    }


def _dedupe_edit_learning_sql_rows(rows: list[Any]) -> list[Any]:
    """Keep newest sqlite row per ``sent_message_id`` (max event id wins)."""
    best_by_mid: dict[str, Any] = {}
    no_mid: list[Any] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if is_bounce_edit_learning_payload(payload):
            continue
        mid = str(payload.get("sent_message_id") or "").strip()
        if not mid:
            no_mid.append(row)
            continue
        prev = best_by_mid.get(mid)
        if prev is None or int(row["id"]) > int(prev["id"]):
            best_by_mid[mid] = row
    merged = sorted(best_by_mid.values(), key=lambda r: int(r["id"]))
    merged.extend(no_mid)
    merged.sort(key=lambda r: int(r["id"]))
    return merged


def edit_learning_dedupe_key(
    *,
    identity_id: Optional[int],
    campaign_id: Optional[str],
    payload: dict[str, Any],
) -> str:
    """Stable key for collapsing reconcile-retried ``draft_edit_learning`` rows."""
    mid = str(payload.get("sent_message_id") or "").strip()
    if mid:
        return f"mid:{mid}"
    iid = int(identity_id or 0)
    cid = str(campaign_id or "")
    dist = payload.get("edit_distance")
    return f"pair:{iid}:{cid}:{dist}"


def dedupe_edit_learning_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep newest row per Gmail ``sent_message_id`` (input order newest-first)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event_type") != "draft_edit_learning":
            out.append(ev)
            continue
        if is_bounce_edit_learning_event(ev):
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        key = edit_learning_dedupe_key(
            identity_id=ev.get("identity_id"),
            campaign_id=ev.get("campaign_id"),
            payload=payload,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def list_learning_events(
    conn: sqlite3.Connection,
    *,
    env: str,
    event_types: tuple[str, ...] = LEARNING_EVENT_TYPES,
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
    goal: Optional[str] = None,
    limit: int = 100,
    before_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return learning events newest-first.

    ``before_id`` enables keyset pagination (returns rows with ``id < before_id``).

    When ``draft_edit_learning`` is requested, duplicate reconcile retries
    (same ``payload.sent_message_id``) are collapsed to the newest row so UI
    and distill batch counts are not inflated by pre-fix CAL rows.
    """
    limit = max(1, min(int(limit), 500))
    needs_dedupe = "draft_edit_learning" in event_types
    fetch_limit = min(500, max(limit * 20, limit)) if needs_dedupe else limit
    placeholders = ",".join("?" * len(event_types))
    where = [f"env = ?", f"event_type IN ({placeholders})"]
    args: list[Any] = [env, *event_types]
    if identity_id is not None:
        where.append("identity_id = ?")
        args.append(int(identity_id))
    if campaign_id is not None:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    if goal is not None:
        where.append("goal = ?")
        args.append(goal)
    if before_id is not None:
        where.append("id < ?")
        args.append(int(before_id))
    sql = (
        "SELECT id, identity_id, campaign_id, event_type, goal, lane, "
        "actor, ts, payload_json, env FROM kol_conversation_events "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    )
    args.append(fetch_limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        raw = d.pop("payload_json", None)
        try:
            d["payload"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            d["payload"] = {}
        out.append(d)
    if needs_dedupe:
        failed_pairs = delivery_failed_edit_learning_pairs(conn, env=env)
        out = [e for e in out if not is_bounce_edit_learning_event(e)]
        out = exclude_delivery_failed_edit_events(out, failed_pairs)
        out = dedupe_edit_learning_events(out)
    return out[:limit]


def list_fact_corrections(
    conn: sqlite3.Connection,
    *,
    env: str,
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Find facts where a manual write superseded an email classifier write."""
    limit = max(1, min(int(limit), 500))
    where = [
        "f.env = ?",
        "(f.source = 'manual' OR f.source LIKE 'manual:%')",
    ]
    args: list[Any] = [env]
    if identity_id is not None:
        where.append("f.identity_id = ?")
        args.append(int(identity_id))
    if campaign_id is not None:
        where.append("f.campaign_id = ?")
        args.append(campaign_id)
    sql = f"""
        SELECT f.id AS manual_id, f.identity_id, f.campaign_id,
               f.fact_key, f.fact_value AS manual_value, f.source AS manual_source,
               f.captured_at AS manual_at,
               e.id AS email_id, e.fact_value AS email_value, e.source AS email_source,
               e.captured_at AS email_at
          FROM kol_facts f
          JOIN kol_facts e
            ON e.identity_id = f.identity_id
           AND IFNULL(e.campaign_id, '') = IFNULL(f.campaign_id, '')
           AND e.fact_key = f.fact_key
           AND e.env = f.env
           AND e.source LIKE 'email:%'
           AND e.id < f.id
         WHERE {' AND '.join(where)}
         ORDER BY f.id DESC
         LIMIT ?
    """
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        for key in ("manual_value", "email_value"):
            try:
                d[key] = json.loads(d[key]) if d.get(key) else None
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


def list_negotiation_history(
    conn: sqlite3.Connection,
    *,
    env: str,
    campaign_id: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Aggregate compensation-related facts per (identity, campaign)."""
    limit = max(1, min(int(limit), 2000))
    keys = (
        "offer.latest_requested_amount",
        "offer.latest_counter_amount",
        "offer.compensation_agreed",
        "offer.kol_paid_quote",
        "offer.compensation_mode",
    )
    placeholders = ",".join("?" * len(keys))
    where = ["env = ?", f"fact_key IN ({placeholders})"]
    args: list[Any] = [env, *keys]
    if campaign_id is not None:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    sql = (
        "SELECT identity_id, campaign_id, fact_key, fact_value, captured_at, id "
        f"FROM kol_facts WHERE {' AND '.join(where)} ORDER BY id DESC"
    )
    rows = conn.execute(sql, args).fetchall()
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    seen_keys: dict[tuple[int, str], set[str]] = {}
    for row in rows:
        d = dict(row)
        iid = int(d["identity_id"])
        cid = str(d["campaign_id"] or "")
        bucket_key = (iid, cid)
        fact_key = str(d["fact_key"])
        seen = seen_keys.setdefault(bucket_key, set())
        if fact_key in seen:
            continue
        seen.add(fact_key)
        bucket = grouped.setdefault(
            bucket_key,
            {"identity_id": iid, "campaign_id": cid, "facts": {}, "env": env},
        )
        val = d.get("fact_value")
        try:
            bucket["facts"][fact_key] = json.loads(val) if val else None
        except (TypeError, ValueError):
            bucket["facts"][fact_key] = val
        if "relationship" not in bucket:
            rel = conn.execute(
                "SELECT preferred_mode, reputation_score, total_collabs, "
                "negotiation_style FROM kol_relationship WHERE identity_id=?",
                (iid,),
            ).fetchone()
            if rel:
                bucket["relationship"] = dict(rel)
        if len(grouped) >= limit:
            break
    return list(grouped.values())


def _parse_event_ts(raw: Any) -> Optional[_dt.datetime]:
    """Parse a ``kol_conversation_events.ts`` value to an aware UTC datetime."""
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    # SQLite ``datetime('now')`` yields ``YYYY-MM-DD HH:MM:SS`` (space, no tz).
    for candidate in (text, text.replace(" ", "T")):
        try:
            dt = _dt.datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.astimezone(_dt.timezone.utc)
        except ValueError:
            continue
    return None


def learning_window_days() -> int:
    """Sliding window for distill sampling; 0 disables (use all unconsumed)."""
    raw = os.environ.get("KOL_LEARNING_WINDOW_DAYS", "90").strip()
    try:
        return max(0, min(int(raw), 3650))
    except ValueError:
        return 0


def filter_events_within_days(
    events: list[dict[str, Any]], days: int,
) -> list[dict[str, Any]]:
    """Keep only events whose ``ts`` is within the last ``days`` (0 = no filter).

    Avoids stale samples dominating distill once the operator's style/strategy
    has moved on.
    """
    if not days or days <= 0:
        return events
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    out: list[dict[str, Any]] = []
    for ev in events:
        dt = _parse_event_ts(ev.get("ts"))
        if dt is None or dt >= cutoff:
            # Keep events with unparseable ts (be permissive, don't silently drop).
            out.append(ev)
    return out


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile (pct in [0,1]); None for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    rank = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return round(ordered[rank], 4)


def _bucket_key(dt: _dt.datetime, bucket: str) -> str:
    if bucket == "day":
        return dt.strftime("%Y-%m-%d")
    # ISO week, e.g. 2026-W23
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _summarize_edit_distances(
    distances: list[float], edited_flags: list[bool]
) -> dict[str, Any]:
    count = len(distances)
    edited_count = sum(1 for f in edited_flags if f)
    return {
        "count": count,
        "edited_count": edited_count,
        "was_edited_rate": round(edited_count / count, 4) if count else None,
        "avg_edit_distance": round(sum(distances) / count, 4) if count else None,
        "p50_edit_distance": _percentile(distances, 0.5),
        "p90_edit_distance": _percentile(distances, 0.9),
    }


def edit_distance_trend(
    conn: sqlite3.Connection,
    *,
    env: str,
    days: int = 90,
    bucket: str = "week",
    goal: Optional[str] = None,
    child_skill: Optional[str] = None,
    operator_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Convergence metric: aggregate ``draft_edit_learning`` edit_distance over time.

    The intent is to answer "is the operator editing AI drafts less over time?"
    (lower ``avg_edit_distance`` / ``was_edited_rate`` = learning is working).

    Args:
        env: CAL partition (LIVE/TEST).
        days: lookback window; events older than this are ignored.
        bucket: ``"week"`` (ISO week) or ``"day"`` time bucket.
        goal: optional filter on the event ``goal`` column.
        child_skill: optional filter on ``payload.child_skill``.
        operator_user_id: optional filter on ``payload.operator_user_id``.

    Returns:
        Dict with ``overall`` summary, ordered ``buckets`` list, and
        ``recent_vs_prior`` (last-bucket avg vs the average of earlier buckets)
        suitable for the regression guard.
    """
    bucket = bucket if bucket in ("day", "week") else "week"
    days = max(1, min(int(days), 730))
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)

    where = ["env = ?", "event_type = 'draft_edit_learning'"]
    args: list[Any] = [env]
    if goal:
        where.append("goal = ?")
        args.append(goal)
    sql = (
        "SELECT id, identity_id, campaign_id, ts, goal, payload_json "
        "FROM kol_conversation_events "
        f"WHERE {' AND '.join(where)} ORDER BY id ASC"
    )
    rows = conn.execute(sql, args).fetchall()
    rows = _dedupe_edit_learning_sql_rows(rows)
    failed_pairs = delivery_failed_edit_learning_pairs(conn, env=env)

    bucketed: dict[str, dict[str, list[Any]]] = {}
    all_distances: list[float] = []
    all_edited: list[bool] = []
    for row in rows:
        dt = _parse_event_ts(row["ts"])
        if dt is None or dt < cutoff:
            continue
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            continue
        if is_bounce_edit_learning_payload(payload):
            continue
        pair = _edit_learning_pair_key(row["identity_id"], row["campaign_id"])
        if pair in failed_pairs:
            continue
        if child_skill and str(payload.get("child_skill") or "") != child_skill:
            continue
        if operator_user_id is not None and int(
            payload.get("operator_user_id") or 0
        ) != int(operator_user_id):
            continue
        dist = payload.get("edit_distance")
        try:
            dist = float(dist)
        except (TypeError, ValueError):
            continue
        was_edited = bool(payload.get("was_edited"))
        key = _bucket_key(dt, bucket)
        slot = bucketed.setdefault(key, {"distances": [], "edited": []})
        slot["distances"].append(dist)
        slot["edited"].append(was_edited)
        all_distances.append(dist)
        all_edited.append(was_edited)

    buckets_out: list[dict[str, Any]] = []
    for key in sorted(bucketed):
        slot = bucketed[key]
        summary = _summarize_edit_distances(slot["distances"], slot["edited"])
        buckets_out.append({"bucket": key, **summary})

    recent_vs_prior: dict[str, Any] = {
        "recent_avg": None, "prior_avg": None, "delta": None
    }
    if len(buckets_out) >= 2:
        recent = buckets_out[-1].get("avg_edit_distance")
        prior_vals = [
            b["avg_edit_distance"]
            for b in buckets_out[:-1]
            if b.get("avg_edit_distance") is not None
        ]
        prior = round(sum(prior_vals) / len(prior_vals), 4) if prior_vals else None
        delta = (
            round(recent - prior, 4)
            if recent is not None and prior is not None
            else None
        )
        recent_vs_prior = {"recent_avg": recent, "prior_avg": prior, "delta": delta}

    return {
        "env": env,
        "days": days,
        "bucket": bucket,
        "goal": goal,
        "child_skill": child_skill,
        "operator_user_id": operator_user_id,
        "overall": _summarize_edit_distances(all_distances, all_edited),
        "buckets": buckets_out,
        "recent_vs_prior": recent_vs_prior,
    }


def edit_distance_since_last_style_approval(
    conn: sqlite3.Connection,
    *,
    env: str,
    last_approval_at: Optional[str] = None,
    days: int = 90,
) -> dict[str, Any]:
    """Compare avg edit distance after the latest approved batch vs before it.

    Used for the regression guard so a worsening trend is tied to policy changes,
    not only «last calendar week vs earlier weeks».
    """
    days = max(1, min(int(days), 730))
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    last_dt = _parse_event_ts(last_approval_at) if last_approval_at else None
    if last_dt is None:
        return {"usable": False, "reason": "no_last_approval_at"}

    where = ["env = ?", "event_type = 'draft_edit_learning'"]
    args: list[Any] = [env]
    rows = conn.execute(
        "SELECT id, identity_id, campaign_id, ts, payload_json "
        "FROM kol_conversation_events "
        f"WHERE {' AND '.join(where)} ORDER BY id ASC",
        args,
    ).fetchall()
    rows = _dedupe_edit_learning_sql_rows(rows)
    failed_pairs = delivery_failed_edit_learning_pairs(conn, env=env)

    after: list[float] = []
    before: list[float] = []
    for row in rows:
        dt = _parse_event_ts(row["ts"])
        if dt is None or dt < cutoff:
            continue
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not payload.get("was_edited"):
            continue
        if is_bounce_edit_learning_payload(payload):
            continue
        pair = _edit_learning_pair_key(row["identity_id"], row["campaign_id"])
        if pair in failed_pairs:
            continue
        try:
            dist = float(payload.get("edit_distance"))
        except (TypeError, ValueError):
            continue
        if dt > last_dt:
            after.append(dist)
        elif dt <= last_dt:
            before.append(dist)

    after_avg = round(sum(after) / len(after), 4) if after else None
    before_avg = round(sum(before) / len(before), 4) if before else None
    delta = (
        round(after_avg - before_avg, 4)
        if after_avg is not None and before_avg is not None
        else None
    )
    usable = (
        delta is not None
        and len(after) >= 1
        and len(before) >= 1
    )
    return {
        "usable": usable,
        "last_approval_at": last_approval_at,
        "after_avg": after_avg,
        "before_avg": before_avg,
        "after_count": len(after),
        "before_count": len(before),
        "delta": delta,
    }


def event_volume_trend(
    conn: sqlite3.Connection,
    *,
    env: str,
    event_type: str,
    days: int = 90,
    bucket: str = "week",
) -> dict[str, Any]:
    """Count learning events per time bucket (for multi-channel overview charts)."""
    bucket = bucket if bucket in ("day", "week") else "week"
    days = max(1, min(int(days), 730))
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    rows = conn.execute(
        """SELECT ts FROM kol_conversation_events
            WHERE env=? AND event_type=? ORDER BY id ASC""",
        (env, event_type),
    ).fetchall()
    counts: dict[str, int] = defaultdict(int)
    total = 0
    for row in rows:
        dt = _parse_event_ts(row["ts"])
        if dt is None or dt < cutoff:
            continue
        counts[_bucket_key(dt, bucket)] += 1
        total += 1
    buckets_out = [{"bucket": k, "count": counts[k]} for k in sorted(counts)]
    return {"event_type": event_type, "days": days, "bucket": bucket, "total": total, "buckets": buckets_out}


def slice_policy_md_for_goals(content_md: str, active_goals: list[str]) -> str:
    """Return only ``## <goal>`` sections matching ``active_goals``."""
    text = (content_md or "").strip()
    if not text or not active_goals:
        return text
    wanted = {g.strip() for g in active_goals if g and g.strip()}
    if not wanted:
        return text

    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        match = _GOAL_SECTION.match(line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            sections.setdefault(current, []).append(line)

    picked: list[str] = []
    matched_any_goal = False
    for goal in sorted(wanted):
        body_lines = sections.get(goal)
        if not body_lines:
            continue
        matched_any_goal = True
        picked.append(f"## {goal}")
        picked.extend(body_lines)
    if not matched_any_goal:
        # Do not fall back to full policy — avoids leaking other goals' tactics.
        return ""
    header = "\n".join(preamble).strip()
    if header:
        picked.insert(0, header)
    return "\n".join(picked).strip()


def build_learning_hints(
    conn: sqlite3.Connection,
    *,
    env: str,
    active_goals: list[str],
    max_chars: int = 4000,
) -> dict[str, Any]:
    """Compose runtime hints from reply_learning policy + recent reject events."""
    from . import policies as pol

    hints: list[dict[str, Any]] = []
    goals = [g for g in active_goals if g]

    policy = pol.get_policy(conn, scope=REJECT_LEARNING_SCOPE, env=env)
    policy_md = (policy or {}).get("content_md") or ""
    if policy_md.strip() and goals:
        filtered = slice_policy_md_for_goals(policy_md, goals)
        if filtered.strip():
            hints.append({
                "source": "policy",
                "scope": REJECT_LEARNING_SCOPE,
                "content": filtered.strip()[:max_chars],
            })

    strat_policy = pol.get_policy(conn, scope=REPLY_STRATEGY_SCOPE, env=env)
    strat_md = (strat_policy or {}).get("content_md") or ""
    if strat_md.strip() and goals:
        from . import learning_distill as _ld

        strat_md = _ld.strip_proposal_context_notes(strat_md)
        filtered_strat = slice_policy_md_for_goals(strat_md, goals)
        if filtered_strat.strip():
            hints.append({
                "source": "policy",
                "scope": REPLY_STRATEGY_SCOPE,
                "content": filtered_strat.strip()[:max_chars],
            })

    # Outcome learning (won/lost root-cause guidance) — goal-sliced advisory.
    outcome_policy = pol.get_policy(conn, scope=OUTCOME_STRATEGY_SCOPE, env=env)
    outcome_md = (outcome_policy or {}).get("content_md") or ""
    if outcome_md.strip() and goals:
        filtered_outcome = slice_policy_md_for_goals(outcome_md, goals)
        if filtered_outcome.strip():
            hints.append({
                "source": "policy",
                "scope": OUTCOME_STRATEGY_SCOPE,
                "content": filtered_outcome.strip()[:max_chars],
            })

    # Company-wide style is cross-goal tone guidance (not goal-sectioned), so
    # surface the whole active doc. Toggle off via KOL_STYLE_IN_HINTS=0 if the
    # style-loader already injects it on the drafting side.
    if _env_flag("KOL_STYLE_IN_HINTS", default=True):
        style_policy = pol.get_policy(conn, scope="company_style")
        style_md = (style_policy or {}).get("content_md") or ""
        if style_md.strip():
            from . import learning_distill as _ld

            style_md = _ld.strip_proposal_context_notes(style_md)
            hints.append({
                "source": "policy",
                "scope": "company_style",
                "content": style_md.strip()[:max_chars],
            })

    for goal in goals:
        events = list_learning_events(
            conn,
            env=env,
            event_types=("draft_rejected_learning",),
            goal=goal,
            limit=5,
        )
        for ev in events:
            payload = ev.get("payload") or {}
            hints.append({
                "source": "reject_event",
                "goal": goal,
                "child_skill": payload.get("child_skill"),
                "tags": payload.get("tags") or [],
                "note": payload.get("note") or "",
                "suggested_fix": payload.get("suggested_fix") or "",
                "snippet": (payload.get("agent_body") or "")[:500],
            })

    # Trim total serialized size
    total = 0
    trimmed: list[dict[str, Any]] = []
    for item in hints:
        chunk = json.dumps(item, ensure_ascii=False)
        if total + len(chunk) > max_chars:
            break
        trimmed.append(item)
        total += len(chunk)
    return {"hints": trimmed, "active_goals": goals}
