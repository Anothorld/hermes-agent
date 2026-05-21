"""Gmail reply-poller — DISABLED in Phase A.

Phase A (v2.4) tore down the legacy ``kol_drafts`` / ``kol_replies``
schema. The new email pipeline (classifier → next-action router) lives
in Phase B. Until that lands, this module exposes a no-op
``run_forever`` so ``serve.py`` continues to import and start cleanly,
plus a one-shot reconcile stub that always returns 0.

Re-enable via the new schema in Phase B.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

log = logging.getLogger(__name__)


async def run_forever() -> None:
    log.info("[gmail_poller] disabled (Phase A); awaiting Phase B rewrite")
    while True:
        await asyncio.sleep(3600)


def reconcile_sent_drafts_once(client, *, envs: Iterable[str] = ("LIVE",)) -> int:  # noqa: ARG001
    log.info("[gmail_poller] reconcile is a no-op until Phase B")
    return 0
