"""Post-collaboration outcome learning (result-level / retrospective).

Two tiers:

* **Tier 1 (threshold = 1):** on each archived collab, run a single-case
  root-cause analysis (why won/lost, why price moved, performance) and record
  a structured ``collab_outcome_learning`` event. Capture only — no policy write.
* **Tier 2 (stratified threshold):** once enough retros exist for a segment
  (>= ``KOL_OUTCOME_LEARNING_BATCH_SIZE`` collabs OR >= ``KOL_OUTCOME_LEARNING_MIN_FAILURES``
  failures, whichever first), distill cross-case patterns into a pending
  ``approval.outcome_learning_proposal`` that merges into the ``outcome_strategy``
  policy on approval (same human gate as style learning).

Outcome guidance is **advisory** and injected per-goal via ``learning_hints``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Optional

from . import cal
from . import learning_llm
from . import learning_store
from . import policies as pol

OUTCOME_LEARNING_EVENT = "collab_outcome_learning"
OUTCOME_STRATEGY_SCOPE = "outcome_strategy"
OUTCOME_LEARNING_APPROVAL_FACT = "approval.outcome_learning_proposal"
ARCHIVAL_OUTCOME_FACT = "approval.archival_outcome"

# Outcome strings that count as a failed / lost collaboration.
_FAILURE_TOKENS = ("fail", "lost", "declined", "rejected", "no_deal", "dropped", "ghosted")
_SUCCESS_TOKENS = ("success", "won", "completed", "delivered", "live", "published")


def classify_outcome_class(outcome: Any) -> str:
    """Map a free-form outcome string to success / failure / partial."""
    text = str(outcome or "").strip().lower()
    if not text:
        return "partial"
    if any(tok in text for tok in _FAILURE_TOKENS):
        return "failure"
    if any(tok in text for tok in _SUCCESS_TOKENS):
        return "success"
    return "partial"


def outcome_batch_size() -> int:
    raw = os.environ.get("KOL_OUTCOME_LEARNING_BATCH_SIZE", "5").strip()
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        return 5


def outcome_min_failures() -> int:
    raw = os.environ.get("KOL_OUTCOME_LEARNING_MIN_FAILURES", "3").strip()
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        return 3


def _segment_for(goal: Optional[str], lane: Optional[str]) -> str:
    """Segment retros by lane/goal so guidance is targeted, not global."""
    return str(goal or lane or "general")


def _retro_segment(ev: dict[str, Any]) -> str:
    payload = ev.get("payload") or {}
    return str(payload.get("segment") or _segment_for(ev.get("goal"), ev.get("lane")))


def _group_retros_by_segment(
    retros: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in retros:
        grouped[_retro_segment(ev)].append(ev)
    return grouped


def _pick_segment_batch(
    grouped: dict[str, list[dict[str, Any]]],
) -> Optional[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    """Choose the first segment that meets the stratified threshold.

    Prefer segments with more failures, then larger totals, for stable ordering.
    """
    eligible: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    for segment, evs in grouped.items():
        met, gate = _outcome_threshold_met(evs)
        if met:
            eligible.append((segment, evs, gate))
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (-item[2]["failures"], -item[2]["total"], item[0]),
    )
    return eligible[0]


# ---------------------------------------------------------------------------
# Tier 1 — per-collab retrospective capture
# ---------------------------------------------------------------------------


def list_archived_collabs(
    conn, *, env: str, limit: int = 200,
) -> list[dict[str, Any]]:
    """Archived collabs (one per identity+campaign) newest-first."""
    rows = conn.execute(
        """SELECT identity_id, campaign_id, fact_value, captured_at
             FROM kol_facts_latest
            WHERE fact_namespace='approval' AND fact_key=? AND env=?
            ORDER BY id DESC LIMIT ?""",
        (ARCHIVAL_OUTCOME_FACT, env, max(1, min(int(limit), 1000))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row["fact_value"]
        try:
            outcome = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            outcome = raw
        out.append({
            "identity_id": row["identity_id"],
            "campaign_id": row["campaign_id"],
            "outcome": outcome,
            "captured_at": row["captured_at"],
        })
    return out


def has_outcome_learning_event(
    conn, *, env: str, identity_id: int, campaign_id: Optional[str],
) -> bool:
    if campaign_id:
        row = conn.execute(
            """SELECT 1 FROM kol_conversation_events
                WHERE env=? AND event_type=? AND identity_id=? AND campaign_id=?
                LIMIT 1""",
            (env, OUTCOME_LEARNING_EVENT, int(identity_id), campaign_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT 1 FROM kol_conversation_events
                WHERE env=? AND event_type=? AND identity_id=? AND campaign_id IS NULL
                LIMIT 1""",
            (env, OUTCOME_LEARNING_EVENT, int(identity_id)),
        ).fetchone()
    return row is not None


def build_collab_retro_sample(
    conn, *, identity_id: int, campaign_id: Optional[str], env: str, outcome: Any = None,
) -> dict[str, Any]:
    """Assemble root-cause context for one archived collaboration."""
    cid = str(campaign_id) if campaign_id else None
    timeline = learning_store.list_conversation_timeline(
        conn, identity_id=identity_id, campaign_id=cid, env=env, limit=40,
    )
    timeline_chrono = [
        learning_store.shape_timeline_event_for_llm(t) for t in reversed(timeline)
    ]
    facts = learning_store.latest_facts_snapshot(
        conn, identity_id=identity_id, campaign_id=cid, env=env,
    )
    negotiations = [
        n for n in learning_store.list_negotiation_history(conn, env=env, campaign_id=cid)
        if int(n.get("identity_id") or 0) == int(identity_id)
    ]
    try:
        escalations = cal.list_escalations(
            env=env, identity_id=identity_id, campaign_id=cid,
        )
    except Exception:
        escalations = []
    relationship = cal.get_relationship(identity_id) or {}
    if outcome is None:
        outcome = facts.get(ARCHIVAL_OUTCOME_FACT)
    goal = facts.get("__active_goal__")  # best-effort; usually absent
    return {
        "identity_id": identity_id,
        "campaign_id": cid,
        "outcome": outcome,
        "outcome_class": classify_outcome_class(outcome),
        "relationship": {
            k: relationship.get(k)
            for k in (
                "negotiation_style", "preferred_mode", "reputation_score",
                "avg_revision_rounds", "total_collabs", "last_outcome",
            )
        },
        "current_facts": facts,
        "negotiations": negotiations,
        "escalations": [
            {
                "reason": e.get("reason"),
                "severity": e.get("severity"),
                "state": e.get("state"),
                "question_to_operator": e.get("question_to_operator"),
            }
            for e in (escalations or [])
        ],
        "conversation_timeline": timeline_chrono,
        "goal_hint": goal,
    }


def _fallback_retro(sample: dict[str, Any]) -> dict[str, Any]:
    """Deterministic root-cause when no LLM is configured."""
    oc = sample.get("outcome_class") or "partial"
    tags: list[str] = []
    esc = sample.get("escalations") or []
    if esc:
        tags.append("had_escalation")
    negs = sample.get("negotiations") or []
    if negs:
        tags.append("price_negotiated")
    rel = sample.get("relationship") or {}
    if rel.get("negotiation_style") == "hard_anchor":
        tags.append("hard_anchor")
    return {
        "outcome_class": oc,
        "root_cause_tags": tags or ["insufficient_signal"],
        "what_worked": [],
        "what_failed": [],
        "price_summary": "",
        "forward_guidance": [],
        "llm_used": False,
    }


def analyze_one_collab_outcome(
    conn,
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    outcome: Any = None,
    updated_by: str = "learning:outcome",
) -> dict[str, Any]:
    """Tier 1: root-cause one collab and write a ``collab_outcome_learning`` event."""
    if has_outcome_learning_event(
        conn, env=env, identity_id=identity_id, campaign_id=campaign_id,
    ):
        return {
            "skipped": True,
            "reason": "outcome_learning_event_exists",
            "identity_id": identity_id,
            "campaign_id": campaign_id,
        }
    sample = build_collab_retro_sample(
        conn, identity_id=identity_id, campaign_id=campaign_id, env=env, outcome=outcome,
    )
    prompt = (
        "You are a KOL partnership analyst. Analyze ONE finished collaboration and\n"
        "explain why it succeeded or failed and how to improve future flows.\n"
        "Return ONLY compact JSON with keys: outcome_class (success|failure|partial),\n"
        "root_cause_tags (array of short snake_case tags e.g. price_too_high, slow_response,\n"
        "competitor_won, scope_mismatch, great_fit, fast_close, quality_issue),\n"
        "what_worked (array of short strings), what_failed (array of short strings),\n"
        "price_summary (one sentence on the price/negotiation trajectory),\n"
        "forward_guidance (array of 1-3 short actionable rules for similar future KOLs).\n"
        "Base every claim on the SAMPLE; do not invent.\n\n"
        f"SAMPLE_JSON:\n{json.dumps(sample, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
        text = learning_llm.strip_markdown_fences(raw).strip()
        # tolerate leading prose before the JSON object
        start = text.find("{")
        end = text.rfind("}")
        parsed = json.loads(text[start : end + 1]) if start >= 0 and end > start else {}
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("empty/invalid LLM JSON")
        result = {
            "outcome_class": str(
                parsed.get("outcome_class") or sample.get("outcome_class") or "partial"
            ),
            "root_cause_tags": list(parsed.get("root_cause_tags") or []),
            "what_worked": list(parsed.get("what_worked") or []),
            "what_failed": list(parsed.get("what_failed") or []),
            "price_summary": str(parsed.get("price_summary") or ""),
            "forward_guidance": list(parsed.get("forward_guidance") or []),
            "llm_used": True,
        }
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "LLM collab-outcome analysis failed; using deterministic fallback",
            exc_info=True,
        )
        result = _fallback_retro(sample)

    payload = {
        **result,
        "campaign_id": sample.get("campaign_id"),
        "segment": _segment_for(sample.get("goal_hint"), None),
        "negotiation_style": (sample.get("relationship") or {}).get("negotiation_style"),
    }
    event_id = cal.write_event(
        identity_id=identity_id,
        campaign_id=campaign_id,
        event_type=OUTCOME_LEARNING_EVENT,
        goal=None,
        lane="meta",
        actor=updated_by,
        payload=payload,
        env=env,
    )
    return {"event_id": event_id, **result}


def analyze_pending_collab_outcomes(
    conn, *, env: str, limit: int = 100, updated_by: str = "learning:outcome",
) -> dict[str, Any]:
    """Tier 1 batch: analyze archived collabs lacking a retro event."""
    archived = list_archived_collabs(conn, env=env, limit=limit)
    analyzed: list[dict[str, Any]] = []
    skipped = 0
    for row in archived:
        iid = int(row["identity_id"])
        cid = row.get("campaign_id")
        if has_outcome_learning_event(conn, env=env, identity_id=iid, campaign_id=cid):
            skipped += 1
            continue
        out = analyze_one_collab_outcome(
            conn,
            identity_id=iid,
            campaign_id=cid,
            env=env,
            outcome=row.get("outcome"),
            updated_by=updated_by,
        )
        analyzed.append({"identity_id": iid, "campaign_id": cid, **out})
    return {
        "env": env,
        "archived_seen": len(archived),
        "analyzed_count": len(analyzed),
        "skipped_existing": skipped,
        "analyzed": analyzed,
    }


# ---------------------------------------------------------------------------
# Tier 2 — cross-case pattern synthesis → approval → outcome_strategy policy
# ---------------------------------------------------------------------------


def list_outcome_retro_events(
    conn, *, env: str, limit: int = 200,
) -> list[dict[str, Any]]:
    return learning_store.list_learning_events(
        conn, env=env, event_types=(OUTCOME_LEARNING_EVENT,), limit=limit,
    )


def list_consumed_outcome_event_ids(conn, *, env: str) -> set[int]:
    """Outcome-retro event ids already folded into an approved proposal."""
    rows = conn.execute(
        """SELECT fact_value FROM kol_facts_latest
            WHERE fact_namespace='approval' AND fact_key=? AND env=?""",
        (OUTCOME_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    consumed: set[int] = set()
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(val, dict) or val.get("decision") != "approved":
            continue
        for eid in val.get("source_event_ids") or []:
            try:
                consumed.add(int(eid))
            except (TypeError, ValueError):
                continue
    return consumed


def pending_outcome_reserved_event_ids(
    conn,
    *,
    env: str,
) -> set[int]:
    """Retro event ids in a pending outcome proposal (not yet approved)."""
    pending = find_pending_outcome_proposal(conn, env=env)
    if not pending:
        return set()
    val = pending.get("value") or {}
    reserved: set[int] = set()
    for raw in val.get("source_event_ids") or []:
        try:
            reserved.add(int(raw))
        except (TypeError, ValueError):
            continue
    return reserved


def find_pending_outcome_proposal(conn, *, env: str) -> Optional[dict[str, Any]]:
    rows = conn.execute(
        """SELECT identity_id, campaign_id, fact_value, captured_at
             FROM kol_facts_latest
            WHERE fact_namespace='approval' AND fact_key=? AND env=?
            ORDER BY id DESC""",
        (OUTCOME_LEARNING_APPROVAL_FACT, env),
    ).fetchall()
    for row in rows:
        try:
            val = json.loads(row["fact_value"]) if row["fact_value"] else {}
        except (TypeError, ValueError):
            continue
        if isinstance(val, dict) and val.get("decision") in (None, "pending"):
            return {"identity_id": row["identity_id"], "value": val}
    return None


def _outcome_threshold_met(retros: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    """Stratified gate: >= batch_size collabs OR >= min_failures failures."""
    batch_size = outcome_batch_size()
    min_failures = outcome_min_failures()
    failures = sum(
        1 for e in retros
        if str((e.get("payload") or {}).get("outcome_class") or "") == "failure"
    )
    total = len(retros)
    met = total >= batch_size or failures >= min_failures
    return met, {
        "total": total,
        "failures": failures,
        "batch_size": batch_size,
        "min_failures": min_failures,
    }


def _aggregate_outcome_markdown(retros: list[dict[str, Any]]) -> str:
    """Deterministic fallback synthesis grouped by outcome_class."""
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in retros:
        payload = ev.get("payload") or {}
        by_class[str(payload.get("outcome_class") or "partial")].append(payload)
    lines = ["## Approved outcome learning", ""]
    for cls in ("failure", "success", "partial"):
        items = by_class.get(cls) or []
        if not items:
            continue
        lines.append(f"### {cls}")
        tag_counts: dict[str, int] = defaultdict(int)
        guidance: list[str] = []
        for it in items:
            for t in it.get("root_cause_tags") or []:
                tag_counts[str(t)] += 1
            for g in it.get("forward_guidance") or []:
                if g not in guidance:
                    guidance.append(str(g))
        if tag_counts:
            top = sorted(tag_counts.items(), key=lambda kv: -kv[1])[:6]
            lines.append("- common causes: " + ", ".join(f"{k}({v})" for k, v in top))
        for g in guidance[:6]:
            lines.append(f"- guidance: {g}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _recent_outcome_rejection_feedback_block(
    conn,
    *,
    env: str,
    segment: Optional[str] = None,
    limit: int = 5,
) -> str:
    try:
        events = learning_store.list_learning_events(
            conn, env=env, event_types=("outcome_proposal_rejected",), limit=limit,
        )
    except Exception:
        return ""
    lines: list[str] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if segment and str(payload.get("segment") or "") not in ("", segment):
            continue
        note = str(payload.get("note") or "").strip()
        tags = payload.get("tags") or []
        if note or tags:
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {note or '(no note)'}{tag_str}")
    if not lines:
        return ""
    return (
        "PREVIOUSLY REJECTED OUTCOME PROPOSALS (do NOT repeat; address the reason):\n"
        + "\n".join(lines[:limit])
        + "\n\n"
    )


def distill_outcome_learning_llm(
    conn,
    retros: list[dict[str, Any]],
    *,
    env: str,
    segment: Optional[str] = None,
) -> tuple[str, bool]:
    """LLM-synthesize cross-case outcome guidance markdown (goal-sectioned)."""
    cur = pol.get_policy(conn, scope=OUTCOME_STRATEGY_SCOPE, env=env)
    baseline = ((cur or {}).get("content_md") or "").strip()[:2000]
    payloads = [e.get("payload") or {} for e in retros]
    rejected_block = _recent_outcome_rejection_feedback_block(
        conn, env=env, segment=segment,
    )
    prompt = (
        "You analyze finished KOL collaborations (root-cause retrospectives) to improve\n"
        "future outreach/negotiation flows. Produce markdown guidance for operators.\n\n"
        f"{rejected_block}"
        "CURRENT outcome_strategy (baseline — output INCREMENTAL delta, do NOT restate):\n"
        f"{baseline or '(none yet)'}\n\n"
        "Output ONLY markdown. Start with `## Approved outcome learning`, then subsections\n"
        "per **goal** where possible (e.g. compensation_negotiation, interest_qualification);\n"
        "3-8 bullets each: why deals are won/lost, pricing lessons, performance signals, and\n"
        "concrete forward rules. Use ADJUST:/REMOVE: prefixes to revise baseline rules.\n"
        "Cite evidence (outcome_class, tags); do not invent.\n\n"
        f"RETROSPECTIVES_JSON:\n{json.dumps(payloads, indent=2, ensure_ascii=False)}"
    )
    try:
        raw = learning_llm.invoke_learning_llm(prompt)
        md = learning_llm.strip_markdown_fences(raw).strip()
        if not md:
            raise RuntimeError("empty outcome markdown")
        return md, True
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "LLM outcome distill failed; using deterministic aggregate", exc_info=True,
        )
        return _aggregate_outcome_markdown(retros), False


def propose_outcome_learning_approval(
    conn, *, env: str, updated_by: str, limit: int = 200,
) -> dict[str, Any]:
    """Tier 2: distill outcome retros into a pending approval proposal."""
    if find_pending_outcome_proposal(conn, env=env):
        return {"skipped": True, "reason": "pending outcome proposal already exists"}
    events = list_outcome_retro_events(conn, env=env, limit=limit)
    consumed = list_consumed_outcome_event_ids(conn, env=env)
    fresh = [e for e in events if int(e.get("id") or 0) not in consumed]
    # Sliding window (Stage D): keep recent retros so guidance reflects current market.
    fresh = learning_store.filter_events_within_days(
        fresh, learning_store.learning_window_days(),
    )
    if not fresh:
        return {"skipped": True, "reason": "no new outcome retros", "events_seen": len(events)}
    grouped = _group_retros_by_segment(fresh)
    picked = _pick_segment_batch(grouped)
    if not picked:
        # Global gate for operator-facing progress (any segment close?).
        _met, gate = _outcome_threshold_met(fresh)
        return {"skipped": True, "reason": "below_outcome_threshold", **gate}
    segment, batch, gate = picked
    batch.sort(key=lambda e: int(e.get("id") or 0))
    proposed_md, llm_used = distill_outcome_learning_llm(
        conn, batch, env=env, segment=segment,
    )
    event_ids = [int(e["id"]) for e in batch if e.get("id") is not None]
    anchor_id = next(
        (int(e["identity_id"]) for e in fresh if e.get("identity_id") is not None), None
    )
    if anchor_id is None:
        return {"skipped": True, "reason": "no identity anchor for outcome proposal"}

    proposal = {
        "decision": "pending",
        "scope": OUTCOME_STRATEGY_SCOPE,
        "segment": segment,
        "env": env,
        "title": f"Outcome learning ({segment}, {env})",
        "proposed_markdown": proposed_md,
        "source_event_ids": event_ids,
        "sample_count": len(batch),
        "failure_count": gate["failures"],
        "llm_used": llm_used,
        "opened_by": updated_by,
    }
    cal.write_facts(
        identity_id=anchor_id,
        campaign_id=None,
        namespace="approval",
        facts={OUTCOME_LEARNING_APPROVAL_FACT: proposal},
        source="learning:propose:outcome",
        env=env,
    )
    return {
        "approval_fact": OUTCOME_LEARNING_APPROVAL_FACT,
        "identity_id": anchor_id,
        "segment": segment,
        "sample_count": len(batch),
        "failure_count": gate["failures"],
        "llm_used": llm_used,
        "source_event_ids": event_ids,
        "pending": True,
    }


def apply_approved_outcome_proposal(
    conn, *, env: str, proposal: dict[str, Any], updated_by: str,
) -> dict[str, Any]:
    """Merge an approved outcome proposal into the ``outcome_strategy`` policy."""
    from . import learning_distill

    proposed = str(proposal.get("proposed_markdown") or "").strip()
    if not proposed:
        raise ValueError("outcome proposal missing proposed_markdown")
    current = pol.get_policy(conn, scope=OUTCOME_STRATEGY_SCOPE, env=env)
    merged = learning_distill.merge_outcome_policy_content(
        (current or {}).get("content_md") or "", proposed,
    )
    row = pol.put_policy(
        conn,
        scope=OUTCOME_STRATEGY_SCOPE,
        content_md=merged,
        updated_by=updated_by,
        title=f"Outcome strategy learning ({env})",
        env=env,
    )
    return {
        "scope": OUTCOME_STRATEGY_SCOPE,
        "version": row.get("version"),
        "policy_id": row.get("id"),
        "merged_chars": len(merged),
    }


__all__ = [
    "OUTCOME_LEARNING_EVENT",
    "OUTCOME_STRATEGY_SCOPE",
    "OUTCOME_LEARNING_APPROVAL_FACT",
    "ARCHIVAL_OUTCOME_FACT",
    "classify_outcome_class",
    "outcome_batch_size",
    "outcome_min_failures",
    "list_archived_collabs",
    "has_outcome_learning_event",
    "build_collab_retro_sample",
    "analyze_one_collab_outcome",
    "analyze_pending_collab_outcomes",
    "list_outcome_retro_events",
    "list_consumed_outcome_event_ids",
    "find_pending_outcome_proposal",
    "distill_outcome_learning_llm",
    "propose_outcome_learning_approval",
    "apply_approved_outcome_proposal",
]
