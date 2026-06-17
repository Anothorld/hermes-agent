"""PII masking for cs-ops-bridge facts and event payloads."""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, MutableMapping

_EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"
)
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_STREET_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\-\s]{3,40}\b(?:Street|St|Avenue|Ave|Road|Rd|"
    r"Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl)\b",
    re.IGNORECASE,
)

_PII_KEY_HINTS = (
    "email",
    "phone",
    "mobile",
    "address",
    "street",
    "ssn",
    "credit",
    "card",
    "payment",
)


def _mask_email(match: re.Match[str]) -> str:
    raw = match.group(0)
    local, _, domain = raw.partition("@")
    if not domain:
        return "[redacted-email]"
    shown = local[:1] if local else ""
    return f"{shown}***@{domain}"


def mask_string(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    out = _EMAIL_RE.sub(_mask_email, text)
    out = _PHONE_RE.sub("[redacted-phone]", out)
    out = _CC_RE.sub("[redacted-card]", out)
    out = _STREET_RE.sub("[redacted-address]", out)
    return out


def _should_mask_key(key: str) -> bool:
    lower = key.lower()
    return any(h in lower for h in _PII_KEY_HINTS)


def sanitize_value(value: Any, *, force_mask: bool = False) -> Any:
    if isinstance(value, str):
        return mask_string(value) if force_mask or value else value
    if isinstance(value, Mapping):
        return sanitize_mapping(value)
    if isinstance(value, list):
        return [sanitize_value(v, force_mask=force_mask) for v in value]
    return value


def sanitize_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in data.items():
        force = _should_mask_key(str(key))
        out[key] = sanitize_value(val, force_mask=force)
    return out


def sanitize_namespaces(
    namespaces: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    sanitized: dict[str, dict[str, Any]] = {}
    adjustments: list[str] = []
    for ns, kv in namespaces.items():
        clean_ns = sanitize_mapping(dict(kv))
        if clean_ns != dict(kv):
            adjustments.append(f"namespace:{ns}")
        sanitized[str(ns)] = clean_ns
    return sanitized, adjustments
