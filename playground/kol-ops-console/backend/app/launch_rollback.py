"""Rollback helpers when async gateway launch jobs fail."""

from __future__ import annotations

import logging
import sqlite3

from .config import get_settings
from .db import _connect

log = logging.getLogger(__name__)


def _open() -> sqlite3.Connection:
    return _connect(get_settings().db_path)


def rollback_pending_registry(*, pending_run_id: str | None) -> None:
    """Remove a placeholder ``product_campaign_runs`` row."""
    if not pending_run_id:
        return
    conn = _open()
    try:
        conn.execute(
            "DELETE FROM product_campaign_runs WHERE run_id=?",
            (pending_run_id,),
        )
        conn.commit()
    finally:
        conn.close()


async def rollback_campaign_start_failure(
    *,
    campaign_id: str,
    env: str,
    pending_run_id: str | None,
) -> None:
    """Mark a failed async campaign start and drop the pending registry row."""
    if not pending_run_id:
        return
    conn = _open()
    try:
        conn.execute(
            "DELETE FROM product_campaign_runs WHERE run_id=?",
            (pending_run_id,),
        )
        conn.execute(
            """UPDATE product_campaigns
               SET status='failed', floor_unmet_reason='gateway_launch_failed'
               WHERE campaign_id=? AND env=? AND run_id=?""",
            (campaign_id, env, pending_run_id),
        )
        conn.commit()
        log.warning(
            "rollback campaign start campaign_id=%s env=%s pending=%s",
            campaign_id,
            env,
            pending_run_id,
        )
    finally:
        conn.close()


async def rollback_rediscover_failure(
    *,
    campaign_id: str,
    env: str,
    pending_run_id: str | None,
    previous_run_id: str | None,
    previous_status: str | None,
) -> None:
    """Restore campaign row after a failed async rediscover."""
    conn = _open()
    try:
        if pending_run_id:
            conn.execute(
                "DELETE FROM product_campaign_runs WHERE run_id=?",
                (pending_run_id,),
            )
        if previous_run_id is not None and previous_status is not None:
            conn.execute(
                """UPDATE product_campaigns
                   SET run_id=?, status=?, floor_unmet_reason=NULL
                   WHERE campaign_id=? AND env=?""",
                (previous_run_id, previous_status, campaign_id, env),
            )
        else:
            conn.execute(
                """UPDATE product_campaigns
                   SET status='failed', floor_unmet_reason='gateway_launch_failed'
                   WHERE campaign_id=? AND env=?""",
                (campaign_id, env),
            )
        conn.commit()
        log.warning(
            "rollback rediscover campaign_id=%s env=%s pending=%s",
            campaign_id,
            env,
            pending_run_id,
        )
    finally:
        conn.close()


async def rollback_discover_email_failure(*, pending_run_id: str | None) -> None:
    """Drop pending registry row for a failed discover-email accept."""
    rollback_pending_registry(pending_run_id=pending_run_id)
