"""Main inbound poll loop — one tick across all operator mailboxes."""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..gmail_client import GmailUnavailable
from ..gmail_console import list_operator_gmail_clients
from .deps import InboundDeps
from .processor import process_message
from .recovery import needs_reprocess_after_global_seen
from .schemas import InboundTickStats, ProcessResult, ProcessStatus
from .state import (
    clear_retry_backoff,
    global_message_seen,
    load_state,
    record_global_message_seen,
    record_retry_backoff,
    retry_not_before,
    save_state,
    state_lock,
    trim_seen,
)

log = logging.getLogger(__name__)


def _record_message_outcome(
    *,
    state: dict,
    seen_key: str,
    seen: set[str],
    env: str,
    message_id: str,
    mailbox_user_id: int,
    status: ProcessStatus,
    preserve_backoff: bool = False,
) -> None:
    """Persist seen markers immediately so a later failure cannot lose progress."""
    if status not in ("dispatched", "skipped"):
        return
    if not preserve_backoff:
        clear_retry_backoff(state, env=env, message_id=message_id)
    seen.add(message_id)
    record_global_message_seen(
        env=env,
        message_id=message_id,
        mailbox_user_id=mailbox_user_id,
    )
    state[seen_key] = trim_seen(seen)
    state[f"last_run_{env}"] = int(time.time())
    save_state(state)


def run_once(
    *,
    env: str,
    lookback_days: int,
    max_results: int,
    deps: Optional[InboundDeps] = None,
) -> dict[str, int]:
    """Poll INBOX once for all connected mailboxes."""
    resolved = deps or InboundDeps.in_process_default()
    mailboxes = list_operator_gmail_clients()
    if not mailboxes:
        raise GmailUnavailable("Gmail token / google_api.py unavailable")

    with state_lock():
        state = load_state()
        matched = 0
        skipped = 0
        retry = 0
        errors = 0
        scanned = 0
        deferred = 0
        query = f"in:inbox newer_than:{int(lookback_days)}d -from:me"
        for mb in mailboxes:
            seen_key = f"seen_{env}_{mb.user_id}"
            seen: set[str] = set(state.get(seen_key, []))
            messages = mb.client.search(query=query, max_results=max_results)
            scanned += len(messages)
            for stub in messages:
                locally_seen = stub.message_id in seen
                if not locally_seen and retry_not_before(
                    state, env=env, message_id=stub.message_id,
                ) > time.time():
                    deferred += 1
                    continue
                if locally_seen:
                    try:
                        full = mb.client.get_message(stub.message_id)
                    except GmailUnavailable as exc:
                        log.warning("gmail get %s failed: %s", stub.message_id, exc)
                        retry += 1
                        continue
                    if not needs_reprocess_after_global_seen(
                        full,
                        env=env,
                        bridge=resolved.bridge,
                    ):
                        continue
                else:
                    globally_seen = global_message_seen(
                        env=env, message_id=stub.message_id,
                    )
                    if globally_seen:
                        seen.add(stub.message_id)
                    try:
                        full = mb.client.get_message(stub.message_id)
                    except GmailUnavailable as exc:
                        log.warning("gmail get %s failed: %s", stub.message_id, exc)
                        retry += 1
                        continue
                    if globally_seen and not needs_reprocess_after_global_seen(
                        full,
                        env=env,
                        bridge=resolved.bridge,
                    ):
                        continue
                try:
                    outcome = process_message(
                        full,
                        env=env,
                        client=mb.client,
                        deps=resolved,
                        mailbox_user_id=mb.user_id,
                        mailbox_email=mb.google_email,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "process_message crashed msg=%s: %s",
                        stub.message_id,
                        exc,
                    )
                    errors += 1
                    retry += 1
                    record_retry_backoff(state, env=env, message_id=full.message_id)
                    continue

                if isinstance(outcome, ProcessResult):
                    status = outcome.status
                    gateway_only_retry = outcome.gateway_only_retry
                else:
                    status = outcome
                    gateway_only_retry = False

                if status == "retry":
                    record_retry_backoff(state, env=env, message_id=full.message_id)
                    state[f"last_run_{env}"] = int(time.time())
                    save_state(state)
                    retry += 1
                    continue

                if status == "skipped" and gateway_only_retry:
                    # Retry cap — do not mark globally seen; operator may re-dispatch.
                    skipped += 1
                    state[f"last_run_{env}"] = int(time.time())
                    save_state(state)
                    continue

                _record_message_outcome(
                    state=state,
                    seen_key=seen_key,
                    seen=seen,
                    env=env,
                    message_id=full.message_id,
                    mailbox_user_id=mb.user_id,
                    status=status,
                    preserve_backoff=gateway_only_retry,
                )
                if gateway_only_retry and status == "dispatched":
                    record_retry_backoff(state, env=env, message_id=full.message_id)
                    state[f"last_run_{env}"] = int(time.time())
                    save_state(state)
                if status == "dispatched":
                    matched += 1
                elif status == "skipped":
                    skipped += 1
            state[seen_key] = trim_seen(seen)
        state[f"last_run_{env}"] = int(time.time())
        save_state(state)
        stats = InboundTickStats(
            matched=matched,
            skipped=skipped,
            retry=retry,
            scanned=scanned,
            mailboxes=len(mailboxes),
            errors=errors,
            deferred=deferred,
        )
        return stats.as_dict()
