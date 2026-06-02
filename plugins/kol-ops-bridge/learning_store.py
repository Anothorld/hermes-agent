"""Read helpers for KOL learning loops (events, facts, policies).

Pure DB reads via an open sqlite3 connection or cal wrappers — no HTTP.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Final, Optional

LEARNING_EVENT_TYPES = (
    "draft_rejected_learning",
    "draft_edit_learning",
)

REJECT_LEARNING_SCOPE = "reply_learning"
REPLY_STRATEGY_SCOPE = "reply_strategy"
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

def style_learning_batch_size() -> int:
    """Min ``draft_edit_learning`` (was_edited) events before LLM distill runs."""
    raw = os.environ.get("KOL_STYLE_LEARNING_BATCH_SIZE", "10").strip()
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        return 10


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


def list_learning_events(
    conn: sqlite3.Connection,
    *,
    env: str,
    event_types: tuple[str, ...] = LEARNING_EVENT_TYPES,
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
    goal: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return learning events newest-first."""
    limit = max(1, min(int(limit), 500))
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
        filtered_strat = slice_policy_md_for_goals(strat_md, goals)
        if filtered_strat.strip():
            hints.append({
                "source": "policy",
                "scope": REPLY_STRATEGY_SCOPE,
                "content": filtered_strat.strip()[:max_chars],
            })

    # Company-wide style is cross-goal tone guidance (not goal-sectioned), so
    # surface the whole active doc. Toggle off via KOL_STYLE_IN_HINTS=0 if the
    # style-loader already injects it on the drafting side.
    if _env_flag("KOL_STYLE_IN_HINTS", default=True):
        style_policy = pol.get_policy(conn, scope="company_style")
        style_md = (style_policy or {}).get("content_md") or ""
        if style_md.strip():
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
