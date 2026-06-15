"""Shared open-escalation / persist-reply-draft CLI normalizers."""

from __future__ import annotations

from typing import Any, Optional


def normalize_open_escalation_body(body: dict[str, Any]) -> dict[str, Any]:
    """Map SKILL-era field names to Bridge ``EscalationOpenBody``."""
    if not str(body.get("reason") or "").strip():
        for alt in ("rule_id", "matched_rule_id"):
            raw = body.get(alt)
            if raw is not None and str(raw).strip():
                body["reason"] = str(raw).strip()
                break
    if not str(body.get("goal") or "").strip() and body.get("goal_name"):
        body["goal"] = str(body["goal_name"]).strip()
    return body


def maybe_attach_linked_escalation_id(
    client: Any,
    body: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """When exactly one open escalation exists, set ``linked_escalation_id``."""
    if body.get("linked_escalation_id") is not None:
        return None
    identity_id = body.get("identity_id")
    campaign_id = body.get("campaign_id")
    env = body.get("env") or "LIVE"
    if not isinstance(identity_id, int) or not campaign_id:
        return None
    resp = client.request(
        "GET",
        "/escalations",
        params={
            "env": env,
            "state": "awaiting_answer",
            "identity_id": identity_id,
            "campaign_id": campaign_id,
        },
    )
    rows = resp.get("escalations") if isinstance(resp, dict) else None
    if not isinstance(rows, list):
        rows = []
    open_rows = [r for r in rows if isinstance(r, dict) and r.get("id") is not None]
    if len(open_rows) == 1:
        body["linked_escalation_id"] = int(open_rows[0]["id"])
        return None
    if len(open_rows) > 1:
        return {
            "error": "ambiguous_open_escalation",
            "hint": (
                "Multiple escalations are awaiting_answer for this "
                "identity+campaign. Pass linked_escalation_id in persist JSON "
                "or resolve extras first."
            ),
            "open_escalation_ids": [int(r["id"]) for r in open_rows],
        }
    return None
