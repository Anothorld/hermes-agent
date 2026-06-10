"""Shortlist-decision learning channel — capture operator approve/remove/transfer.

Each operator decision on a shortlist candidate (approve / remove / transfer)
is persisted as one ``shortlist_decision_learning`` row in
``kol_conversation_events`` together with a **frozen KOL feature snapshot**
taken at decision time (facts can change later; the sample must not).

Nightly distill (:mod:`learning_discovery`) groups these events by SKU and by
product category to learn discovery criteria baselines.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sqlite3
from typing import Any, Final, Optional

from . import cal
from . import discovery_decision_tags as decision_tags
from . import learning_store

log = logging.getLogger(__name__)

SHORTLIST_DECISION_EVENT: Final[str] = "shortlist_decision_learning"
DISCOVERY_LEARNING_APPROVAL_FACT: Final[str] = "approval.discovery_learning_proposal"

# Frozen-snapshot allowlist: keys (exact or prefix ending with a dot/underscore)
# describing the KOL at decision time. Kept small so events stay readable.
_SNAPSHOT_KEY_PREFIXES: Final[tuple[str, ...]] = (
    "identity.creator_type",
    "identity.kol_type",
    "identity.followers",
    "identity.region",
    "identity.language",
    "identity.content_pillars",
    "identity.signature_hooks",
    "identity.voice_descriptors",
    "identity.hero_post",
    "identity.recommendation_reason",
    "identity.nox_",
    "identity.veedcrawl_profile_followers",
    "identity.veedcrawl_recent_reels_stats",
    "identity.veedcrawl_extract_summary",
    "identity.veedcrawl_last_reel_",
)

# Candidate payload score keys worth freezing into the sample.
_CANDIDATE_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    "audience_fit",
    "final_fit",
    "brand_safety",
    "engagement_quality",
    "showcase_score",
    "niche_match",
    "match_score",
    "reason",
    "evidence_url",
)


def comment_min_samples() -> int:
    """SPU sample count below which the operator comment is required."""
    raw = os.environ.get("KOL_DISCOVERY_COMMENT_MIN_SAMPLES", "50").strip()
    try:
        return max(0, min(int(raw), 10_000))
    except ValueError:
        return 50


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _filter_snapshot_keys(facts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in facts.items():
        if any(key.startswith(prefix) for prefix in _SNAPSHOT_KEY_PREFIXES):
            out[key] = val
    return out


def build_kol_snapshot(
    conn: sqlite3.Connection,
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
) -> dict[str, Any]:
    """Frozen feature snapshot for one KOL at decision time.

    Combines the relevant identity/campaign facts (creator type, followers,
    content pillars, Nox diligence, reels stats) with the campaign-candidate
    row (discovery_score, agent scores, relationship status).
    """
    facts = learning_store.latest_facts_snapshot(
        conn, identity_id=identity_id, campaign_id=campaign_id, env=env, max_keys=200,
    )
    snapshot: dict[str, Any] = {"facts": _filter_snapshot_keys(facts)}

    identity = conn.execute(
        "SELECT primary_handle, platform, region, display_name "
        "FROM kol_identity WHERE id=?",
        (int(identity_id),),
    ).fetchone()
    if identity:
        snapshot["identity"] = dict(identity)

    if campaign_id:
        cand = conn.execute(
            """SELECT discovery_score, relationship_status, candidate_status,
                      source, payload_json
                 FROM campaign_candidates
                WHERE campaign_id=? AND identity_id=? AND env=?""",
            (campaign_id, int(identity_id), env),
        ).fetchone()
        if cand:
            candidate: dict[str, Any] = {
                "discovery_score": cand["discovery_score"],
                "relationship_status": cand["relationship_status"],
                "candidate_status": cand["candidate_status"],
                "source": cand["source"],
            }
            try:
                payload = json.loads(cand["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                candidate["scores"] = {
                    k: payload.get(k)
                    for k in _CANDIDATE_PAYLOAD_KEYS
                    if payload.get(k) is not None
                }
            snapshot["candidate"] = candidate
    return snapshot


def record_shortlist_decisions(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    action: str,
    decided_by: str,
    decisions: list[dict[str, Any]],
    operator_user_id: Optional[int] = None,
    sku: Optional[str] = None,
    product_name: Optional[str] = None,
    pitch_excerpt: Optional[str] = None,
    transfer_to_campaign_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write one ``shortlist_decision_learning`` event per decided KOL.

    Args:
        action: ``approve`` | ``remove`` | ``transfer``.
        decisions: list of ``{identity_id, tags, comment}`` — tags/comment may
            be the batch-shared values already resolved per KOL by the caller.

    Returns:
        ``{"event_ids": [...], "recorded": n, "skipped": [...]}`` — identities
        that no longer exist are skipped, never raised (capture must not block
        the operator's main action).
    """
    if action not in decision_tags.DECISION_ACTIONS:
        raise ValueError(f"unknown decision action: {action!r}")
    category = get_category_for_sku(conn, sku=sku) if sku else None
    event_ids: list[int] = []
    skipped: list[dict[str, Any]] = []
    for item in decisions:
        try:
            identity_id = int(item.get("identity_id") or 0)
        except (TypeError, ValueError):
            identity_id = 0
        if identity_id <= 0 or not cal.get_identity(identity_id):
            skipped.append({"identity_id": item.get("identity_id"), "reason": "identity_not_found"})
            continue
        tags = decision_tags.normalize_decision_tags(
            conn, item.get("tags"), action=action,
        )
        comment = str(item.get("comment") or "").strip()[:2000]
        payload: dict[str, Any] = {
            "action": action,
            "reason_tags": tags,
            "comment": comment,
            "campaign_id": campaign_id,
            "sku": sku,
            "category": category,
            "product_name": product_name,
            "pitch_excerpt": (pitch_excerpt or "")[:600] or None,
            "operator_user_id": operator_user_id,
            "transfer_to_campaign_id": transfer_to_campaign_id if action == "transfer" else None,
            "kol_snapshot": build_kol_snapshot(
                conn, identity_id=identity_id, campaign_id=campaign_id, env=env,
            ),
        }
        event_id = cal.write_event(
            identity_id=identity_id,
            campaign_id=campaign_id,
            event_type=SHORTLIST_DECISION_EVENT,
            goal=None,
            lane="meta",
            actor=f"shortlist:{decided_by}",
            payload=payload,
            env=env,
        )
        if event_id is None:
            skipped.append({"identity_id": identity_id, "reason": "write_event_failed"})
            continue
        event_ids.append(int(event_id))
    return {"event_ids": event_ids, "recorded": len(event_ids), "skipped": skipped}


def list_decision_events(
    conn: sqlite3.Connection,
    *,
    env: str,
    sku: Optional[str] = None,
    category: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Decision events newest-first, optionally filtered by sku/category/action."""
    events = learning_store.list_learning_events(
        conn, env=env, event_types=(SHORTLIST_DECISION_EVENT,), limit=limit,
    )
    out: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if sku and str(payload.get("sku") or "") != sku:
            continue
        if category:
            ev_category = str(payload.get("category") or "").strip() or (
                get_category_for_sku(conn, str(payload.get("sku") or "")) or ""
            )
            if ev_category != category:
                continue
        if action and str(payload.get("action") or "") != action:
            continue
        out.append(ev)
    return out


def count_decision_samples(
    conn: sqlite3.Connection,
    *,
    env: str,
    sku: Optional[str] = None,
    category: Optional[str] = None,
) -> int:
    """SQL COUNT (no row materialisation, no implicit limit).

    The comment-required threshold must stay correct even after the SPU has
    accumulated thousands of decision events.
    """
    where = ["env=?", "event_type=?"]
    args: list[Any] = [env, SHORTLIST_DECISION_EVENT]
    if sku:
        where.append("json_extract(payload_json, '$.sku')=?")
        args.append(str(sku))
    if category:
        where.append("json_extract(payload_json, '$.category')=?")
        args.append(str(category))
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM kol_conversation_events WHERE {' AND '.join(where)}",
        args,
    ).fetchone()
    return int(row["n"] if row else 0)


def feedback_requirements(
    conn: sqlite3.Connection,
    *,
    env: str,
    sku: Optional[str],
) -> dict[str, Any]:
    """Whether the comment is still required for this SPU (early-learning phase).

    Tags are always required; the free-text comment is required until the SPU
    has accumulated ``KOL_DISCOVERY_COMMENT_MIN_SAMPLES`` decision samples.
    """
    threshold = comment_min_samples()
    sku_count = count_decision_samples(conn, env=env, sku=sku) if sku else 0
    category = get_category_for_sku(conn, sku=sku) if sku else None
    category_count = (
        count_decision_samples(conn, env=env, category=category) if category else 0
    )
    return {
        "sku": sku,
        "category": category,
        "sku_sample_count": sku_count,
        "category_sample_count": category_count,
        "comment_required_threshold": threshold,
        "comment_required": sku_count < threshold,
        "tags_required": True,
    }


# ---------------------------------------------------------------------------
# Product category map (SKU → category)
# ---------------------------------------------------------------------------


def get_category_for_sku(
    conn: sqlite3.Connection, *, sku: Optional[str],
) -> Optional[str]:
    if not sku:
        return None
    row = conn.execute(
        "SELECT category FROM product_category_map WHERE sku=?", (str(sku),),
    ).fetchone()
    return str(row["category"]) if row else None


def list_product_categories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT sku, category, source, confidence, product_name, updated_by, "
        "updated_at FROM product_category_map ORDER BY sku",
    ).fetchall()
    return [dict(r) for r in rows]


def set_product_category(
    conn: sqlite3.Connection,
    *,
    sku: str,
    category: str,
    source: str,
    updated_by: str,
    confidence: Optional[float] = None,
    product_name: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert a SKU → category mapping.

    Operator-set rows (``source='operator'``) are authoritative: an LLM write
    never overwrites them; an operator write always wins.
    """
    if source not in ("llm", "operator"):
        raise ValueError(f"invalid category source: {source!r}")
    sku = str(sku or "").strip()
    category = str(category or "").strip()
    if not sku or not category:
        raise ValueError("sku and category are required")
    existing = conn.execute(
        "SELECT source FROM product_category_map WHERE sku=?", (sku,),
    ).fetchone()
    if existing and existing["source"] == "operator" and source == "llm":
        return {"sku": sku, "skipped": True, "reason": "operator_override_present"}
    conn.execute(
        """INSERT INTO product_category_map
               (sku, category, source, confidence, product_name, updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(sku) DO UPDATE SET
               category=excluded.category,
               source=excluded.source,
               confidence=excluded.confidence,
               product_name=COALESCE(excluded.product_name, product_category_map.product_name),
               updated_by=excluded.updated_by,
               updated_at=excluded.updated_at""",
        (sku, category[:80], source, confidence, product_name, updated_by, _now()),
    )
    conn.commit()
    return {"sku": sku, "category": category[:80], "source": source, "skipped": False}


__all__ = [
    "DISCOVERY_LEARNING_APPROVAL_FACT",
    "SHORTLIST_DECISION_EVENT",
    "build_kol_snapshot",
    "comment_min_samples",
    "count_decision_samples",
    "feedback_requirements",
    "get_category_for_sku",
    "list_decision_events",
    "list_product_categories",
    "record_shortlist_decisions",
    "set_product_category",
]
