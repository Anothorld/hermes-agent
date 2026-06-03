"""HMAC dispatch claims: LIVE Nox API only after KOL Ops Console materializes config."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from schemas import Gate

from internal.exceptions import NoxCampaignGateError

GATE_FOR_OPERATION: dict[str, Gate] = {
    "diligence_pack": "shortlist_confirm",
    "contacts": "pre_outreach_confirm",
    "creator_search": "supplement_search",
    "monitor_setup": "post_publish_confirm",
}

_DEFAULT_TTL_SECONDS = 4 * 60 * 60  # 4h — covers a single gateway run window


def _dispatch_secret() -> str:
    for key in (
        "NOX_CONSOLE_DISPATCH_SECRET",
        "HERMES_KOL_OPS_BRIDGE_KEY",
        "KOC_BRIDGE_KEY",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _canonical_message(claim: Mapping[str, Any]) -> bytes:
    body = {
        "allowed_gates": sorted(str(g) for g in claim.get("allowed_gates") or []),
        "campaign_id": str(claim.get("campaign_id") or ""),
        "expires_at": str(claim.get("expires_at") or ""),
        "issued_at": str(claim.get("issued_at") or ""),
        "nonce": str(claim.get("nonce") or ""),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def issue_console_dispatch(
    *,
    campaign_id: str,
    allowed_gates: list[str],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Build a signed ``nox_console_dispatch`` claim for Console-written config files."""
    secret = _dispatch_secret()
    if not secret:
        raise NoxCampaignGateError(
            "Cannot issue console dispatch: set NOX_CONSOLE_DISPATCH_SECRET or "
            "HERMES_KOL_OPS_BRIDGE_KEY"
        )
    gates = sorted({str(g).strip() for g in allowed_gates if str(g).strip()})
    if not gates:
        raise NoxCampaignGateError("allowed_gates must not be empty")
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=max(1, ttl_seconds))
    claim: dict[str, Any] = {
        "campaign_id": campaign_id,
        "allowed_gates": gates,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "nonce": uuid4().hex,
    }
    claim["sig"] = hmac.new(
        secret.encode("utf-8"),
        _canonical_message(claim),
        hashlib.sha256,
    ).hexdigest()
    return claim


def attach_console_dispatch(
    campaign_config: dict[str, Any],
    *,
    campaign_id: str,
    allowed_gates: list[str],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Merge a fresh dispatch claim into ``campaign_config`` (in place)."""
    campaign_config["nox_console_dispatch"] = issue_console_dispatch(
        campaign_id=campaign_id,
        allowed_gates=allowed_gates,
        ttl_seconds=ttl_seconds,
    )
    return campaign_config


def verify_console_dispatch(
    campaign_config: Mapping[str, Any],
    *,
    gate: str,
    operation: str,
) -> None:
    """Reject LIVE calls without a valid Console-issued dispatch claim."""
    if os.environ.get("NOX_SKIP_CONSOLE_DISPATCH", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return

    expected_gate = GATE_FOR_OPERATION.get(operation)
    if expected_gate and gate != expected_gate:
        raise NoxCampaignGateError(
            f"gate={gate!r} does not match operation={operation!r} "
            f"(expected {expected_gate!r})"
        )

    claim = campaign_config.get("nox_console_dispatch")
    if not isinstance(claim, dict):
        raise NoxCampaignGateError(
            "LIVE Nox requires nox_console_dispatch in --campaign-config-file "
            "(trigger via KOL Ops Console; agent sessions cannot self-issue)"
        )

    campaign_id = str(campaign_config.get("campaign_id") or "").strip()
    if campaign_id and str(claim.get("campaign_id") or "") != campaign_id:
        raise NoxCampaignGateError("nox_console_dispatch campaign_id mismatch")

    allowed = claim.get("allowed_gates")
    if not isinstance(allowed, list) or gate not in allowed:
        raise NoxCampaignGateError(
            f"gate {gate!r} not in console dispatch allowed_gates={allowed!r}"
        )

    secret = _dispatch_secret()
    if not secret:
        raise NoxCampaignGateError(
            "NOX_CONSOLE_DISPATCH_SECRET or bridge key required to verify dispatch"
        )

    sig = str(claim.get("sig") or "")
    expected = hmac.new(
        secret.encode("utf-8"),
        _canonical_message(claim),
        hashlib.sha256,
    ).hexdigest()
    if not sig or not hmac.compare_digest(sig, expected):
        raise NoxCampaignGateError("nox_console_dispatch signature invalid")

    expires_raw = str(claim.get("expires_at") or "")
    try:
        expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NoxCampaignGateError("nox_console_dispatch expires_at invalid") from exc
    if datetime.now(UTC) >= expires:
        raise NoxCampaignGateError(
            "nox_console_dispatch expired; re-trigger from Console"
        )


__all__ = [
    "GATE_FOR_OPERATION",
    "attach_console_dispatch",
    "issue_console_dispatch",
    "verify_console_dispatch",
]
