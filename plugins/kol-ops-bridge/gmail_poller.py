"""Background Gmail-poller for kol-ops-bridge.

Periodically lists unread Gmail messages, matches each against an
outbound draft (via ``In-Reply-To``, ``References``, or ``threadId``),
and on a hit records a ``reply_received`` event so the operator's reply
monitor lights up without manual injection.

LoD-compliant: knows only about :class:`GmailClient` and the CAL module's
``find_draft_by_*`` / ``record_reply`` / ``record_event`` functions.
Never touches Google libraries directly.

The poller is best-effort:

* On any Gmail error it skips the cycle and tries again next interval.
* On any per-message error it logs and moves to the next message.
* It NEVER raises out of the asyncio task; the bridge is unaffected.

Matching strategy (in order of confidence):

1. ``In-Reply-To`` header → outbound ``gmail_message_id`` (1.00).
2. ``References`` chain   → any outbound ``gmail_message_id`` (0.95).
3. ``threadId``           → outbound ``gmail_thread_id`` (0.90).
4. ``From:`` address      → alias kind=email                  (0.70).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import re
from typing import Optional

from . import cal
from . import gmail_client as _gmail

log = logging.getLogger(__name__)

# Tunables (env-overridable for ops convenience).
DEFAULT_INTERVAL_SEC = float(os.environ.get("KOL_OPS_BRIDGE_POLL_INTERVAL", "60"))
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("KOL_OPS_BRIDGE_POLL_LOOKBACK_DAYS", "1"))
DEFAULT_MAX_RESULTS = int(os.environ.get("KOL_OPS_BRIDGE_POLL_MAX_RESULTS", "25"))

# We track which message ids we've already processed in-memory so we
# don't double-record across cycles. CAL's INSERT OR REPLACE on
# (gmail_message_id) means duplicates are merged anyway, but the in-mem
# cache saves us repeated `gmail get` calls.
_seen_message_ids: set[str] = set()
_SEEN_CACHE_MAX = 1_000


# RFC 2822 angle-addr extraction: "Alice <alice@x.com>" -> alice@x.com
_ANGLE_ADDR = re.compile(r"<([^>]+)>")


def _extract_email(addr: str) -> Optional[str]:
    if not addr:
        return None
    m = _ANGLE_ADDR.search(addr)
    if m:
        return m.group(1).strip().lower()
    addr = addr.strip().lower()
    return addr or None


def _trim_seen() -> None:
    if len(_seen_message_ids) > _SEEN_CACHE_MAX:
        # Drop oldest half — set ordering is insertion-ordered in CPython 3.7+.
        for mid in list(_seen_message_ids)[: _SEEN_CACHE_MAX // 2]:
            _seen_message_ids.discard(mid)


def _match_draft(
    *,
    full_msg: "_gmail.GmailMessage",
    env: str,
) -> tuple[Optional[dict], str, float]:
    """Return (draft_row_or_none, match_strategy, match_confidence)."""
    in_reply_to = (full_msg.in_reply_to or "").strip().strip("<>")
    if in_reply_to:
        draft = cal.find_draft_by_message_id(
            gmail_message_id=in_reply_to, env=env
        )
        if draft:
            return draft, "in_reply_to", 1.00

    references = (full_msg.references or "").strip()
    if references:
        # Header is space-separated list of <msg-id> tokens.
        for token in references.split():
            token = token.strip().strip("<>")
            if not token:
                continue
            draft = cal.find_draft_by_message_id(
                gmail_message_id=token, env=env
            )
            if draft:
                return draft, "references_chain", 0.95

    if full_msg.thread_id:
        draft = cal.find_draft_by_thread_id(
            gmail_thread_id=full_msg.thread_id, env=env
        )
        if draft:
            return draft, "thread_id", 0.90

    sender = _extract_email(full_msg.from_addr)
    if sender:
        identity_id = cal.resolve_identity(
            aliases=[("email", sender)], env=env,
        )
        if identity_id:
            # Synthesize a minimal draft-like dict so the caller can still
            # link the reply to an identity even when no thread is known.
            return (
                {
                    "kol_identity_id": identity_id,
                    "campaign_id": None,
                    "gmail_thread_id": full_msg.thread_id or None,
                },
                "sender_email",
                0.70,
            )

    return None, "unmatched", 0.0


def _process_one_message(
    *,
    client: "_gmail.GmailClient",
    summary: "_gmail.GmailMessage",
    env: str,
) -> bool:
    """Fetch full headers + try to record. Returns True iff matched."""
    if summary.message_id in _seen_message_ids:
        return False
    _seen_message_ids.add(summary.message_id)
    _trim_seen()

    try:
        full = client.get_message(summary.message_id)
    except _gmail.GmailUnavailable as exc:
        log.warning("[gmail-poller] get_message(%s) failed: %s",
                    summary.message_id, exc)
        return False

    draft, strategy, confidence = _match_draft(full_msg=full, env=env)
    if not draft:
        log.debug("[gmail-poller] no match for %s (from=%s)",
                  full.message_id, full.from_addr)
        return False

    received_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    cal.record_reply(
        kol_identity_id=draft["kol_identity_id"],
        gmail_message_id=full.message_id,
        gmail_thread_id=full.thread_id or draft.get("gmail_thread_id"),
        received_at=received_at,
        match_strategy=strategy,
        match_confidence=confidence,
        from_addr=full.from_addr,
        snippet=(full.snippet or full.body[:160] if full.body else None),
        body=full.body,
        campaign_id=draft.get("campaign_id"),
        env=env,
    )
    cal.record_event(
        kol_identity_id=draft["kol_identity_id"],
        event_type="reply_received",
        stage="outreach",
        sub_status="reply_received",
        actor="gmail_poller",
        campaign_id=draft.get("campaign_id"),
        env=env,
        payload={
            "gmail_message_id": full.message_id,
            "match_strategy": strategy,
            "match_confidence": confidence,
        },
    )
    log.info(
        "[gmail-poller] recorded reply msg=%s -> identity=%s strategy=%s",
        full.message_id, draft["kol_identity_id"], strategy,
    )
    return True


async def _poll_once(client: "_gmail.GmailClient") -> None:
    """One polling cycle across both envs."""
    query = f"is:unread newer_than:{DEFAULT_LOOKBACK_DAYS}d"
    # Run blocking subprocess in a worker thread so we don't block the loop.
    try:
        messages = await asyncio.to_thread(
            client.search, query=query, max_results=DEFAULT_MAX_RESULTS,
        )
    except _gmail.GmailUnavailable as exc:
        log.warning("[gmail-poller] search failed: %s", exc)
        return

    if not messages:
        return

    for summary in messages:
        # We don't know which env a reply belongs to up front, so we try
        # LIVE first then TEST. The matcher will skip mis-env drafts.
        for env in ("LIVE", "TEST"):
            try:
                matched = await asyncio.to_thread(
                    _process_one_message,
                    client=client, summary=summary, env=env,
                )
                if matched:
                    break
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[gmail-poller] process_one_message crashed for %s/%s: %s",
                    summary.message_id, env, exc,
                )


async def run_forever(
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    client: Optional["_gmail.GmailClient"] = None,
) -> None:
    """Main loop. Exits only on cancellation."""
    client = client or _gmail.default_client()
    if not client.is_available():
        log.warning(
            "[gmail-poller] disabled — no token at %s. "
            "Run google-workspace setup.py to enable.",
            client.token_path,
        )
        return

    log.info(
        "[gmail-poller] started (interval=%.1fs lookback=%dd max=%d)",
        interval_sec, DEFAULT_LOOKBACK_DAYS, DEFAULT_MAX_RESULTS,
    )
    while True:
        try:
            await _poll_once(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("[gmail-poller] cycle crashed: %s", exc)
        await asyncio.sleep(interval_sec)
