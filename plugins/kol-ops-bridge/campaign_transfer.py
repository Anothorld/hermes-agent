"""Deterministic shortlist campaign transfer (Phase 1a).

Moves a KOL candidate from one campaign's discovery pool to another before
shortlist approval — no archive, Gmail thread, or gateway runs.
"""

from __future__ import annotations

from typing import Any, Optional

from . import cal  # type: ignore[import-not-found]

_SHORTLIST_SOURCE_STATUSES = frozenset({"discovered", "shortlisted"})
_TARGET_BLOCK_STATUSES = frozenset({
    "discovered",
    "shortlisted",
    "selected_for_outreach",
    "needs_review",
})


class CampaignTransferError(ValueError):
    """Raised when a transfer precheck or step fails."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 409,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def transfer_shortlist_candidate(
    *,
    identity_id: int,
    from_campaign_id: str,
    to_campaign_id: str,
    env: str = "LIVE",
    reason: str = "",
    operator_note: str = "",
) -> dict[str, Any]:
    """Reject source shortlist row and upsert the identity into the target pool.

    Idempotent on source when already ``rejected`` with a transfer reason only
    if target already has the row in ``discovered`` (returns success summary).

    Args:
        identity_id: KOL identity to move.
        from_campaign_id: Source campaign (must have a shortlist-eligible row).
        to_campaign_id: Target campaign (must exist in ``campaign_config``).
        env: TEST or LIVE.
        reason: Operator-facing reason (stored in review_reason suffix).
        operator_note: Optional extra note appended to review_reason.

    Returns:
        Summary dict with both campaign ids and resulting candidate_status.

    Raises:
        CampaignTransferError: On validation or CAL failures.
    """
    if env not in ("TEST", "LIVE"):
        raise CampaignTransferError(
            code="invalid_env",
            message=f"env must be TEST or LIVE; got {env!r}",
            status_code=400,
        )
    src = (from_campaign_id or "").strip()
    dst = (to_campaign_id or "").strip()
    if not src or not dst:
        raise CampaignTransferError(
            code="campaign_id_required",
            message="from_campaign_id and to_campaign_id are required",
            status_code=400,
        )
    if src == dst:
        raise CampaignTransferError(
            code="same_campaign",
            message="源活动与目标活动不能相同",
            status_code=400,
        )

    if cal.get_campaign_config(dst, env=env) is None:
        raise CampaignTransferError(
            code="target_campaign_not_found",
            message=f"目标活动 {dst!r} 在 {env} 下不存在，请先启动该活动",
            status_code=404,
            details={"to_campaign_id": dst, "env": env},
        )

    source_row = cal.get_candidate_for(
        identity_id=identity_id,
        campaign_id=src,
        env=env,
    )
    if source_row is None:
        raise CampaignTransferError(
            code="source_candidate_missing",
            message=f"该 KOL 不在活动 {src} 的候选人池中",
            status_code=404,
            details={"from_campaign_id": src, "identity_id": identity_id},
        )

    src_status = str(source_row.get("candidate_status") or "")
    if src_status not in _SHORTLIST_SOURCE_STATUSES:
        raise CampaignTransferError(
            code="source_not_shortlist",
            message=(
                f"仅支持发现后、批准前的转移（discovered/shortlisted）；"
                f"当前状态为 {src_status!r}"
            ),
            status_code=409,
            details={
                "from_campaign_id": src,
                "identity_id": identity_id,
                "candidate_status": src_status,
            },
        )

    target_row = cal.get_candidate_for(
        identity_id=identity_id,
        campaign_id=dst,
        env=env,
    )
    if target_row is not None:
        tgt_status = str(target_row.get("candidate_status") or "")
        if tgt_status in _TARGET_BLOCK_STATUSES:
            raise CampaignTransferError(
                code="target_candidate_exists",
                message=(
                    f"目标活动 {dst} 已有该 KOL（状态 {tgt_status}）。"
                    "请直接在目标活动 shortlist 处理，或先移除/归档该行。"
                ),
                status_code=409,
                details={
                    "to_campaign_id": dst,
                    "identity_id": identity_id,
                    "candidate_status": tgt_status,
                },
            )

    note_parts = [f"transferred_to:{dst}"]
    if reason.strip():
        note_parts.append(reason.strip())
    if operator_note.strip():
        note_parts.append(operator_note.strip())
    review_reason = " | ".join(note_parts)[:500]

    updated = cal.set_candidate_status(
        campaign_id=src,
        identity_ids=[identity_id],
        candidate_status="rejected",
        review_reason=review_reason,
        env=env,
    )
    if updated < 1:
        raise CampaignTransferError(
            code="source_reject_failed",
            message="无法在源活动中标记为已转移",
            status_code=500,
        )

    discovery_score = source_row.get("discovery_score")
    try:
        candidate_id = cal.upsert_candidate(
            campaign_id=dst,
            identity_id=identity_id,
            source="operator_transfer",
            discovery_score=discovery_score if isinstance(discovery_score, (int, float)) else None,
            candidate_status="discovered",
            review_reason=f"transferred_from:{src}",
            env=env,
        )
    except Exception as exc:
        from . import discovery_skip as _ds
        from . import outreach_touch as _ot

        if isinstance(exc, _ds.DiscoverySkipActive):
            raise CampaignTransferError(
                code="discovery_skip_active",
                message=str(exc),
                status_code=409,
                details={
                    "identity_id": exc.identity_id,
                    "reason": exc.reason,
                },
            ) from exc
        if isinstance(exc, _ot.OutreachCooldownActive):
            raise CampaignTransferError(
                code="outreach_cooldown_active",
                message=str(exc),
                status_code=409,
                details={
                    "identity_id": exc.identity_id,
                    "last_touch_at": exc.last_touch_at,
                    "last_touch_campaign_id": exc.last_touch_campaign_id,
                },
            ) from exc
        raise
    if candidate_id is None:
        raise CampaignTransferError(
            code="target_upsert_failed",
            message="无法写入目标活动候选人池",
            status_code=500,
        )

    resolved = cal.resolve_candidate_relationships(campaign_id=dst, env=env)

    return {
        "ok": True,
        "source_stage": "shortlist",
        "identity_id": identity_id,
        "from_campaign_id": src,
        "to_campaign_id": dst,
        "env": env,
        "source_candidate_status": "rejected",
        "target_candidate_status": "discovered",
        "target_candidate_id": candidate_id,
        "relationships_resolved": resolved,
    }


__all__ = ["CampaignTransferError", "transfer_shortlist_candidate"]
