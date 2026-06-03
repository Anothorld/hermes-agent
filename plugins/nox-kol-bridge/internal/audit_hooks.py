"""Emit CAL audit events after successful Nox tool operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from internal.bridge_audit import write_nox_event


@dataclass(frozen=True)
class AuditContext:
    """Optional bridge audit target."""

    campaign_id: str
    identity_id: int
    env: str
    gate: str
    operation: str


def emit_nox_audit(
    result: dict[str, Any],
    ctx: Optional[AuditContext],
) -> dict[str, Any]:
    """Attach ``audit_event`` and best-effort write CAL event."""
    if ctx is None or ctx.identity_id <= 0 or not ctx.campaign_id:
        return result
    cache_hit = bool(result.get("cache_hit"))
    event_type = "nox_cache_hit" if cache_hit else "nox_api_call"
    payload = {
        "gate": ctx.gate,
        "operation": ctx.operation,
        "cache_hit": cache_hit,
        "api_calls": result.get("api_calls", 0),
        "cache_month": result.get("cache_month"),
        "cache_key": result.get("cache_key"),
    }
    result = dict(result)
    bridge_out = write_nox_event(
        event_type=event_type,
        identity_id=ctx.identity_id,
        campaign_id=ctx.campaign_id,
        env=ctx.env,
        payload=payload,
    )
    if bridge_out is not None:
        result["audit_event"] = {
            "event_type": event_type,
            "bridge": bridge_out,
            "status": "written",
        }
    else:
        result["audit_event"] = {
            "event_type": event_type,
            "status": "skipped",
            "reason": "bridge unavailable or HERMES_KOL_OPS_BRIDGE_KEY not set",
            "payload": payload,
        }
    return result
