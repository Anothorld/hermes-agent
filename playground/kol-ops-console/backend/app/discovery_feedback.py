"""Shortlist decision feedback — validation + learning capture.

Shared by the three operator actions (approve shortlist / remove from
shortlist / transfer campaign). Validation is strict (422 with operator-
friendly Chinese messages); capture is **best-effort** — a learning-channel
failure logs a warning and writes an audit row but never rolls back or blocks
the operator's main action.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import datetime as _dt
from typing import Any, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .config import get_settings

log = logging.getLogger(__name__)

FAILED_CAPTURE_ACTION = "learning.shortlist_decision_failed"
REPLAYED_CAPTURE_ACTION = "learning.shortlist_decision_replayed"

PITCH_EXCERPT_CHARS = 400


class KolFeedbackOverride(BaseModel):
    """Per-KOL override inside a batch approve (keyed by handle)."""

    tags: list[str] = Field(default_factory=list)
    comment: Optional[str] = Field(default=None, max_length=2000)


class DecisionFeedbackBody(BaseModel):
    """Operator feedback attached to a shortlist decision.

    For batch approve, ``shared_tags``/``shared_comment`` apply to every
    selected KOL unless a handle appears in ``per_kol_overrides``.
    """

    shared_tags: list[str] = Field(default_factory=list)
    shared_comment: Optional[str] = Field(default=None, max_length=2000)
    per_kol_overrides: dict[str, KolFeedbackOverride] = Field(default_factory=dict)


def get_product_info(
    conn: sqlite3.Connection, *, campaign_id: str, env: str,
) -> dict[str, Any]:
    """Resolve sku / product name / pitch excerpt from console-local tables."""
    row = conn.execute(
        "SELECT pc.sku, p.name, p.pitch_md FROM product_campaigns pc "
        "LEFT JOIN products p ON p.sku = pc.sku "
        "WHERE pc.campaign_id=? AND pc.env=?",
        (campaign_id, env),
    ).fetchone()
    if not row:
        return {"sku": None, "product_name": None, "pitch_excerpt": None}
    pitch = str(row["pitch_md"] or "").strip()
    return {
        "sku": row["sku"],
        "product_name": row["name"],
        "pitch_excerpt": pitch[:PITCH_EXCERPT_CHARS] or None,
    }


async def active_tag_vocabulary(
    bridge: BridgeClient, *, action: str,
) -> Optional[set[str]]:
    """Active tag slugs for this action, or ``None`` when the bridge is down.

    ``None`` (unknown vocabulary) skips strict tag validation so a learning
    outage never blocks the operator's main action.
    """
    try:
        out = await bridge.list_discovery_tags(action=action, status="active")
        return {
            str(t.get("tag") or "").strip().lower()
            for t in (out.get("tags") or [])
            if t.get("tag")
        }
    except Exception as exc:  # noqa: BLE001 — degrade, never block
        log.warning("discovery tag vocabulary unavailable (action=%s): %s", action, exc)
        return None


async def comment_required_for_sku(
    bridge: BridgeClient, *, sku: Optional[str], env: str,
) -> bool:
    """Early-learning phase: comment required until the SPU has enough samples.

    Degrades to ``False`` when the bridge learning endpoint is unreachable so
    a learning-channel outage never blocks shortlist operations.
    """
    try:
        req = await bridge.discovery_feedback_requirements(sku=sku, env=env)
        return bool(req.get("comment_required", True))
    except BridgeError as exc:
        log.warning("discovery feedback requirements unavailable: %s", exc)
        return False


def _feedback_required() -> bool:
    return get_settings().discovery_feedback_required


async def validate_decision_feedback(
    bridge: BridgeClient,
    *,
    feedback: Optional[DecisionFeedbackBody],
    handles: list[str],
    sku: Optional[str],
    env: str,
    action: str,
) -> None:
    """Raise 422 with actionable detail when required feedback is missing.

    Rules (when ``KOC_DISCOVERY_FEEDBACK_REQUIRED`` is on):
    * every KOL must end up with at least one reason tag
      (shared or per-KOL override);
    * the comment is required while the SPU is in the early-learning phase
      (below ``KOL_DISCOVERY_COMMENT_MIN_SAMPLES`` samples).
    """
    if not _feedback_required():
        return
    if not handles:
        # Nothing to learn from (e.g. retry of an already-approved batch).
        return
    if feedback is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "decision_feedback_required",
                "message": "请先为这次操作选择原因标签（学习样本需要），再提交。",
                "action": action,
            },
        )
    missing_tags: list[str] = []
    for handle in handles:
        override = feedback.per_kol_overrides.get(handle)
        tags = (override.tags if override and override.tags else feedback.shared_tags)
        if not tags:
            missing_tags.append(handle)
    if missing_tags:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "code": "decision_tags_required",
                "message": "以下 KOL 还没有原因标签，请勾选至少一个标签后再提交。",
                "handles": missing_tags,
                "action": action,
            },
        )
    # Strict vocabulary check: an unknown tag would be silently normalized to
    # ``other`` by the bridge, losing the operator's intent. Reject up-front
    # so the UI refreshes its tag list. Skipped when vocabulary is unreachable.
    vocab = await active_tag_vocabulary(bridge, action=action)
    if vocab is not None:
        submitted: set[str] = set(feedback.shared_tags)
        for override in feedback.per_kol_overrides.values():
            submitted.update(override.tags)
        invalid = sorted(
            t for t in submitted if str(t).strip().lower() not in vocab
        )
        if invalid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "code": "decision_tags_invalid",
                    "message": "以下标签已失效或不存在，请刷新页面后重新勾选。",
                    "invalid_tags": invalid,
                    "action": action,
                },
            )
    if await comment_required_for_sku(bridge, sku=sku, env=env):
        missing_comments: list[str] = []
        for handle in handles:
            override = feedback.per_kol_overrides.get(handle)
            comment = (
                override.comment
                if override and (override.comment or "").strip()
                else feedback.shared_comment
            )
            if not (comment or "").strip():
                missing_comments.append(handle)
        if missing_comments:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "code": "decision_comment_required",
                    "message": (
                        "学习初期需要您用一句话说明真实理由"
                        "（例如：粉丝画像太低龄化了，我们要的是精致白领。）"
                    ),
                    "handles": missing_comments,
                    "action": action,
                },
            )


def resolve_per_kol_decisions(
    *,
    feedback: Optional[DecisionFeedbackBody],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge shared tags/comment with per-KOL overrides into bridge items.

    ``rows`` are ``{identity_id, handle}`` pairs.
    """
    decisions: list[dict[str, Any]] = []
    for row in rows:
        handle = str(row.get("handle") or "")
        override = feedback.per_kol_overrides.get(handle) if feedback else None
        tags = (
            override.tags
            if override and override.tags
            else (feedback.shared_tags if feedback else [])
        )
        comment = (
            override.comment
            if override and (override.comment or "").strip()
            else (feedback.shared_comment if feedback else None)
        )
        decisions.append({
            "identity_id": row.get("identity_id"),
            "tags": tags,
            "comment": comment,
        })
    return decisions


async def record_decisions_safe(
    bridge: BridgeClient,
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    action: str,
    decided_by: str,
    actor_user_id: Optional[int],
    decisions: list[dict[str, Any]],
    product_info: dict[str, Any],
    transfer_to_campaign_id: Optional[str] = None,
    max_attempts: int = 2,
    retry_delay_sec: float = 0.5,
) -> dict[str, Any]:
    """Persist decision learning events; never raises (graceful degradation).

    Retries one transient bridge failure before giving up (capture runs on
    the operator's request path, so attempts are deliberately capped). On
    final failure the **full request body** is written to the audit log
    (``learning.shortlist_decision_failed``) so the sample can be replayed
    against ``POST /learning/shortlist-decision`` instead of being lost — the
    main operator action has already succeeded at this point and cannot be
    re-driven through the feedback gate.
    """
    body = {
        "campaign_id": campaign_id,
        "env": env,
        "action": action,
        "decided_by": decided_by,
        "operator_user_id": actor_user_id,
        "decisions": decisions,
        "sku": product_info.get("sku"),
        "product_name": product_info.get("product_name"),
        "pitch_excerpt": product_info.get("pitch_excerpt"),
        "transfer_to_campaign_id": transfer_to_campaign_id,
    }
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            return await bridge.record_shortlist_decision(body)
        except Exception as exc:  # noqa: BLE001 — capture must not block the main op
            last_exc = exc
            log.warning(
                "shortlist decision learning capture failed "
                "(attempt=%d/%d action=%s campaign=%s): %s",
                attempt, max_attempts, action, campaign_id, exc,
            )
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay_sec * attempt)
    try:
        write_audit(
            conn,
            actor_user_id=actor_user_id or 0,
            action="learning.shortlist_decision_failed",
            target=campaign_id,
            payload={
                "action": action,
                "error": str(last_exc)[:500],
                "attempts": max_attempts,
                # Full replayable body — recover via the bridge endpoint
                # POST /learning/shortlist-decision.
                "replay_body": body,
            },
        )
    except Exception:  # noqa: BLE001
        log.warning("audit write for failed decision capture also failed", exc_info=True)
    return {"recorded": 0, "error": str(last_exc)}


def list_failed_shortlist_captures(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    include_replayed: bool = False,
) -> list[dict[str, Any]]:
    """Audit rows where learning capture failed after the main action succeeded."""
    rows = conn.execute(
        "SELECT id, target, payload_json, ts FROM audit_log "
        "WHERE action=? ORDER BY id DESC LIMIT ?",
        (FAILED_CAPTURE_ACTION, max(1, min(int(limit), 200))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if payload.get("replayed_at") and not include_replayed:
            continue
        replay_body = payload.get("replay_body")
        if not isinstance(replay_body, dict):
            continue
        decisions = replay_body.get("decisions") or []
        out.append({
            "audit_id": row["id"],
            "ts": row["ts"],
            "target": row["target"],
            "capture_action": payload.get("action"),
            "error": payload.get("error"),
            "decision_count": len(decisions),
            "sku": replay_body.get("sku"),
            "campaign_id": replay_body.get("campaign_id"),
            "env": replay_body.get("env"),
            "replayed_at": payload.get("replayed_at"),
            "replay_event_ids": payload.get("replay_event_ids"),
        })
    return out


async def replay_shortlist_capture(
    bridge: BridgeClient,
    conn: sqlite3.Connection,
    *,
    audit_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    """Replay a failed capture against Bridge ``POST /learning/shortlist-decision``."""
    row = conn.execute(
        "SELECT target, payload_json FROM audit_log WHERE id=? AND action=?",
        (int(audit_id), FAILED_CAPTURE_ACTION),
    ).fetchone()
    if not row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "capture_not_found", "message": "未找到该补录记录。"},
        )
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "capture_invalid", "message": "补录数据损坏，无法重放。"},
        ) from exc
    if payload.get("replayed_at"):
        return {
            "already_replayed": True,
            "replayed_at": payload.get("replayed_at"),
            "replay_event_ids": payload.get("replay_event_ids") or [],
        }
    replay_body = payload.get("replay_body")
    if not isinstance(replay_body, dict):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "capture_missing_body", "message": "补录数据缺少 replay_body。"},
        )
    try:
        result = await bridge.record_shortlist_decision(replay_body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {
                "code": "replay_failed",
                "message": "补录失败，请稍后重试或联系工程。",
                "error": str(exc)[:500],
            },
        ) from exc
    payload["replayed_at"] = _dt_now_iso()
    payload["replay_event_ids"] = result.get("event_ids") or []
    payload["replayed_by_user_id"] = actor_user_id
    conn.execute(
        "UPDATE audit_log SET payload_json=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), int(audit_id)),
    )
    conn.commit()
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action=REPLAYED_CAPTURE_ACTION,
        target=str(row["target"]),
        payload={
            "source_audit_id": audit_id,
            "recorded": result.get("recorded"),
            "event_ids": result.get("event_ids"),
        },
    )
    return {"already_replayed": False, **result}


def _dt_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
