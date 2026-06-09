"""Deterministic identity matching for inbound Gmail messages."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable, Optional

from ..gmail_client import GmailMessage
from .deps import InboundBridgePort, MatchBridgeError
from .event_helpers import (
    event_emails,
    event_message_ids,
    event_subject,
    event_thread_ids,
    event_timestamp_from_event,
    extract_email,
    normalize_subject,
)
from .gating import classify_identity_integrity, derive_content_risk, resolve_autoflow_controls
from .schemas import IdentityMatch

log = logging.getLogger(__name__)

_DETACHED_MATCH_WINDOW_DAYS = 14


def expected_identity_email(bridge: InboundBridgePort, identity_id: int) -> Optional[str]:
    identity = bridge.get_identity(identity_id)
    if not isinstance(identity, dict):
        return None
    primary = identity.get("primary_email")
    if not isinstance(primary, str):
        return None
    normalized = primary.strip().lower()
    return normalized or None


def match_identity(
    msg: GmailMessage,
    *,
    env: str,
    bridge: InboundBridgePort,
) -> Optional[IdentityMatch]:
    """Return enriched identity-match context for an inbound msg or None."""
    try:
        events_list = bridge.list_recent_events(env=env, limit=1000)
    except Exception as exc:  # noqa: BLE001
        log.error("bridge list_recent_events failed: %s", exc)
        raise MatchBridgeError(f"list_recent_events failed: {exc}") from exc

    events: Iterable[dict[str, Any]] = events_list
    strict_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    weak_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    sender_email = extract_email(msg.from_addr)
    norm_subject = normalize_subject(msg.subject)
    now = dt.datetime.now(dt.timezone.utc)
    best_detached_score = -1
    detached_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    detached_candidates: dict[tuple[int, Optional[str]], dict[str, Any]] = {}

    for ev in events:
        if ev.get("env") != env:
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if not isinstance(ev.get("identity_id"), int):
            continue
        identity_id = int(ev["identity_id"])
        campaign_id = ev.get("campaign_id")

        ev_message_ids = event_message_ids(payload)
        ev_thread_ids = event_thread_ids(payload)
        canonical_thread_id = sorted(ev_thread_ids)[0] if ev_thread_ids else None
        if msg.in_reply_to and msg.in_reply_to in ev_message_ids:
            strict_hit = (
                identity_id,
                campaign_id,
                "strict",
                "in_reply_to",
                canonical_thread_id or msg.thread_id or None,
            )
            break
        if msg.thread_id and msg.thread_id in ev_thread_ids and weak_hit is None:
            weak_hit = (
                identity_id,
                campaign_id,
                "weak",
                "thread_id",
                msg.thread_id,
            )

        if not sender_email:
            continue
        ev_emails = event_emails(payload)
        if sender_email not in ev_emails:
            continue
        score = 2
        ev_subject = normalize_subject(event_subject(payload))
        if norm_subject and ev_subject and norm_subject == ev_subject:
            score += 1
        event_dt = event_timestamp_from_event(ev)
        if event_dt and (now - event_dt).days <= _DETACHED_MATCH_WINDOW_DAYS:
            score += 1
        subject_match = bool(norm_subject and ev_subject and norm_subject == ev_subject)
        recent_match = bool(
            event_dt and (now - event_dt).days <= _DETACHED_MATCH_WINDOW_DAYS
        )
        is_outbound_event = str(ev.get("event_type") or "").startswith("outbound_")
        if score > best_detached_score:
            best_detached_score = score
            detached_hit = (
                identity_id,
                campaign_id,
                "detached",
                "heuristic",
                canonical_thread_id or msg.thread_id or None,
            )
        candidate_key = (identity_id, campaign_id)
        current = detached_candidates.get(candidate_key)
        if current is None or score > int(current.get("score", -1)):
            detached_candidates[candidate_key] = {
                "score": score,
                "campaign_id": campaign_id,
                "canonical_thread_id": canonical_thread_id,
                "subject_match": subject_match,
                "recent_match": recent_match,
                "is_outbound_event": is_outbound_event,
            }

    hit = strict_hit or weak_hit
    if hit is None and best_detached_score >= 3 and detached_hit is not None:
        tied = [
            key
            for key, cand in detached_candidates.items()
            if int(cand.get("score", -1)) >= best_detached_score
        ]
        if len(tied) == 1:
            hit = detached_hit
    if hit is None and len(detached_candidates) == 1:
        (only_identity_id, only_campaign_id), candidate = next(iter(detached_candidates.items()))
        if (
            int(candidate.get("score", -1)) >= 3
            and bool(candidate.get("subject_match"))
            and bool(candidate.get("recent_match"))
            and bool(candidate.get("is_outbound_event"))
        ):
            canonical_thread_id = candidate.get("canonical_thread_id")
            hit = (
                only_identity_id,
                only_campaign_id,
                "detached",
                "heuristic_unique_sender",
                str(canonical_thread_id) if canonical_thread_id else (msg.thread_id or None),
            )

    if hit is None:
        return None

    identity_id, campaign_id, thread_integrity, matched_by, history_thread_id = hit
    expected_email = expected_identity_email(bridge, identity_id)
    content_risk, controls = derive_content_risk(msg)
    identity_integrity, reasons = classify_identity_integrity(
        sender_email=sender_email,
        expected_email=expected_email,
        from_header=msg.from_addr,
        body=msg.body,
    )
    allow_autoflow, controls = resolve_autoflow_controls(
        content_risk=content_risk,
        thread_integrity=thread_integrity,
        identity_integrity=identity_integrity,
        controls=controls,
    )
    risk_controls = {"allow_autoflow": allow_autoflow, **controls}
    if thread_integrity == "detached":
        reasons.append("detached_thread_heuristic_match")
    return IdentityMatch(
        identity_id=identity_id,
        campaign_id=campaign_id,
        thread_integrity=thread_integrity,
        matched_by=matched_by,
        history_thread_id=history_thread_id,
        identity_integrity=identity_integrity,
        reasons=sorted(set(reasons)),
        content_risk=content_risk,
        risk_controls=risk_controls,
        sender_email=sender_email,
        expected_email=expected_email,
    )
