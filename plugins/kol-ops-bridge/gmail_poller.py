"""Gmail SENT reconciliation for operator-approved drafts.

Inbound reply dispatching is handled by ``scripts/kol_reply_dispatcher.py``.
This background task only checks Gmail's SENT label and marks approved
drafts as truly sent after an operator sends them in Gmail.
"""

from __future__ import annotations

import asyncio
import os
import logging
from typing import Iterable

from . import cal
from .gmail_client import GmailClient, GmailUnavailable

log = logging.getLogger(__name__)


async def run_forever() -> None:
    interval = max(30, int(os.environ.get("KOL_OPS_SENT_RECONCILE_INTERVAL_SEC", "300")))
    envs = tuple(
        e.strip().upper()
        for e in os.environ.get("KOL_OPS_SENT_RECONCILE_ENVS", "TEST,LIVE").split(",")
        if e.strip().upper() in {"TEST", "LIVE"}
    ) or ("LIVE",)
    client = GmailClient()
    log.info("[gmail_poller] sent reconcile enabled interval=%ss envs=%s", interval, envs)
    while True:
        try:
            count = reconcile_sent_drafts_once(client, envs=envs)
            if count:
                log.info("[gmail_poller] reconciled %s sent draft(s)", count)
        except GmailUnavailable as exc:
            log.warning("[gmail_poller] gmail unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("[gmail_poller] sent reconcile failed: %s", exc)
        await asyncio.sleep(interval)


def reconcile_sent_drafts_once(client, *, envs: Iterable[str] = ("LIVE",)) -> int:
    sent_thread_ids = client.list_sent_thread_ids(lookback_days=14, max_results=200)
    now = cal._now()  # noqa: SLF001 - shared timestamp helper inside the bridge package.
    reconciled = 0
    for env in envs:
        for row in cal.list_approved_reply_drafts(env=env):
            gmail_draft = row.get("gmail_draft") if isinstance(row, dict) else {}
            thread_id = gmail_draft.get("thread_id") if isinstance(gmail_draft, dict) else None
            if not thread_id or thread_id not in sent_thread_ids:
                continue
            identity_id = int(row["identity_id"])
            campaign_id = row.get("campaign_id")
            event_id = cal.write_event(
                identity_id=identity_id,
                campaign_id=campaign_id,
                event_type="outbound_sent",
                goal="outreach",
                lane="commerce",
                actor="gmail:sent-reconcile",
                payload={"thread_id": thread_id, "gmail_draft": gmail_draft},
                env=env,
            )
            cal.write_facts(
                identity_id=identity_id,
                campaign_id=campaign_id,
                namespace="offer",
                facts={
                    "offer.outreach_sent": True,
                    "offer.outreach_sent_at": now,
                    "offer.gmail_sent_thread_id": thread_id,
                },
                source="gmail:sent-reconcile",
                source_event_id=event_id,
                env=env,
            )
            reconciled += 1
    return reconciled
