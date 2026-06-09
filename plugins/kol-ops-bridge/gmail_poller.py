"""Gmail SENT reconciliation for operator-approved drafts.

Inbound reply dispatching is handled by ``scripts/kol_reply_dispatcher.py``.
This background task checks Gmail's SENT label and marks approved drafts as
sent after an operator sends them in Gmail, including edit-learning capture
via :mod:`gmail_reconcile` (same path as learning ``reconcile_sent`` jobs).
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import time
from pathlib import Path
from typing import Iterable, Optional

from .gmail_client import GmailClient, GmailUnavailable
from .gmail_console import list_operator_gmail_clients
from .gmail_reconcile import run_reconcile_all_mailboxes, run_reconcile_sent

log = logging.getLogger(__name__)

_DEBUG_LOG = Path("/Users/arnold/agent_prj/.cursor/debug-a8fb01.log")


def _dbg_log(
    *,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "a8fb01",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


def _lookback_days() -> int:
    return max(7, int(os.environ.get("KOL_OPS_SENT_RECONCILE_LOOKBACK_DAYS", "14")))


def _max_results() -> int:
    return max(50, int(os.environ.get("KOL_OPS_SENT_RECONCILE_MAX_RESULTS", "200")))


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
        t0 = time.perf_counter()
        _dbg_log(
            location="gmail_poller.py:run_forever",
            message="reconcile_start",
            data={"interval_sec": interval, "envs": list(envs)},
            hypothesis_id="H1",
        )
        try:
            count = reconcile_sent_drafts_once(client, envs=envs)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            _dbg_log(
                location="gmail_poller.py:run_forever",
                message="reconcile_end",
                data={
                    "elapsed_ms": elapsed_ms,
                    "reconciled_count": count,
                    "envs": list(envs),
                },
                hypothesis_id="H1",
            )
            if count:
                log.info("[gmail_poller] reconciled %s sent draft(s)", count)
        except GmailUnavailable as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            _dbg_log(
                location="gmail_poller.py:run_forever",
                message="reconcile_gmail_unavailable",
                data={"elapsed_ms": elapsed_ms, "error": str(exc)[:200]},
                hypothesis_id="H1",
            )
            log.warning("[gmail_poller] gmail unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            _dbg_log(
                location="gmail_poller.py:run_forever",
                message="reconcile_failed",
                data={
                    "elapsed_ms": elapsed_ms,
                    "error": type(exc).__name__,
                },
                hypothesis_id="H1",
            )
            log.exception("[gmail_poller] sent reconcile failed: %s", exc)
        await asyncio.sleep(interval)


def reconcile_sent_drafts_once(
    client: Optional[GmailClient] = None,
    *,
    envs: Iterable[str] = ("LIVE",),
) -> int:
    """Delegate to full sent reconcile (body diff + ``draft_edit_learning``)."""
    lookback = _lookback_days()
    max_results = _max_results()
    total = 0
    gmail = client or GmailClient()
    for env in envs:
        env_norm = str(env).strip().upper()
        if env_norm not in {"TEST", "LIVE"}:
            continue
        try:
            if list_operator_gmail_clients():
                summary = run_reconcile_all_mailboxes(
                    env=env_norm,
                    lookback_days=lookback,
                    max_results=max_results,
                )
            else:
                summary = run_reconcile_sent(
                    env=env_norm,
                    lookback_days=lookback,
                    max_results=max_results,
                    client=gmail,
                )
            total += int(summary.get("reconciled_count") or 0)
        except GmailUnavailable:
            raise
    return total
