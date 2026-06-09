"""Content-risk and autoflow soft-gating for inbound replies."""

from __future__ import annotations

import re
from typing import Optional

from ..gmail_client import GmailMessage

_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com",
    "live.com", "proton.me", "protonmail.com",
}
_AGENCY_CUE_RE = re.compile(
    r"\b(agent|agency|management|manager|assistant|team|talent|rep|representative)\b",
    re.IGNORECASE,
)
_PAYMENT_CUE_RE = re.compile(
    r"\b(paypal|wire|bank|swift|iban|payoneer|stripe|crypto|usdt|wallet|invoice|payout)\b",
    re.IGNORECASE,
)
_CONTRACT_CUE_RE = re.compile(
    r"\b(contract|agreement|msa|nda|clause|term[s]?)\b",
    re.IGNORECASE,
)
_BUDGET_CUE_RE = re.compile(
    r"\b(rate|budget|quote|quoted|price|pricing|paid|commission|compensation)\b",
    re.IGNORECASE,
)
_HANDOFF_CUE_RE = re.compile(
    r"\b(contact|reach out|coordinate|follow up).{0,50}\b(agent|manager|assistant|team)\b",
    re.IGNORECASE,
)


def email_domain(value: Optional[str]) -> Optional[str]:
    if not value or "@" not in value:
        return None
    return value.rsplit("@", 1)[-1].strip().lower() or None


def derive_content_risk(msg: GmailMessage) -> tuple[str, dict[str, bool]]:
    haystack = f"{msg.subject}\n{msg.body}".lower()
    gate_budget = bool(_BUDGET_CUE_RE.search(haystack))
    gate_contract = bool(_CONTRACT_CUE_RE.search(haystack))
    gate_payout = bool(_PAYMENT_CUE_RE.search(haystack))
    if _HANDOFF_CUE_RE.search(haystack):
        risk = "c3"
        gate_budget = True
        gate_contract = True
        gate_payout = True
    elif gate_budget or gate_contract or gate_payout:
        risk = "c2"
    else:
        risk = "c1"
    return risk, {
        "gate_budget": gate_budget,
        "gate_contract": gate_contract,
        "gate_payout": gate_payout,
    }


def resolve_autoflow_controls(
    *,
    content_risk: str,
    thread_integrity: str,
    identity_integrity: str,
    controls: dict[str, bool],
) -> tuple[bool, dict[str, bool]]:
    """Return ``(allow_autoflow, updated_controls)`` for reply soft-gating."""
    out = dict(controls)
    allow_autoflow = True
    if content_risk == "c3":
        allow_autoflow = False
    elif thread_integrity == "detached" and (
        out["gate_budget"] or out["gate_contract"] or out["gate_payout"]
    ):
        allow_autoflow = False
    elif (
        identity_integrity == "delegated"
        and out["gate_budget"]
        and not (out["gate_contract"] or out["gate_payout"])
        and thread_integrity != "detached"
    ):
        allow_autoflow = True
        out["gate_budget"] = False
    elif identity_integrity in {"delegated", "unknown"} and (
        out["gate_budget"] or out["gate_contract"] or out["gate_payout"]
    ):
        allow_autoflow = False
    return allow_autoflow, out


def classify_identity_integrity(
    *,
    sender_email: Optional[str],
    expected_email: Optional[str],
    from_header: str,
    body: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not expected_email:
        reasons.append("identity_primary_email_missing")
        return "unknown", reasons
    if sender_email == expected_email:
        return "matched", reasons
    sender_domain = email_domain(sender_email)
    expected_domain = email_domain(expected_email)
    if sender_domain and expected_domain and sender_domain == expected_domain:
        if sender_domain in _PERSONAL_EMAIL_DOMAINS:
            reasons.append("same_provider_domain_not_authoritative")
            return "drifted", reasons
        reasons.append("same_domain_alias")
        return "drifted", reasons
    if _AGENCY_CUE_RE.search(from_header) or _AGENCY_CUE_RE.search(body):
        reasons.append("agency_cue_detected")
        return "delegated", reasons
    reasons.append("sender_email_mismatch")
    return "drifted", reasons
