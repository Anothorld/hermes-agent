"""Periodic cleanup of ESC vault blobs (ref_count + TTL)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cal
from .escalation_attachment_vault import vault_dir

log = logging.getLogger(__name__)

_LINK_TTL_HOURS = int(os.environ.get("CS_OPS_ESC_VAULT_LINK_TTL_HOURS", "24"))
_BLOB_TTL_HOURS = int(os.environ.get("CS_OPS_ESC_VAULT_BLOB_TTL_HOURS", "168"))
_CLEANUP_INTERVAL_SEC = int(os.environ.get("CS_OPS_ESC_VAULT_CLEANUP_INTERVAL_SEC", "3600"))


def _iso_cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def run_vault_cleanup_once() -> dict[str, int]:
    link_cutoff = _iso_cutoff(_LINK_TTL_HOURS)
    blob_cutoff = _iso_cutoff(_BLOB_TTL_HOURS)
    links_removed = 0
    blobs_removed = 0

    for link in cal.list_stale_vault_links(escalation_resolved_before=link_cutoff):
        if cal.delete_vault_link(link_id=str(link["id"])):
            links_removed += 1

    root = vault_dir() / "blobs"
    for blob in cal.list_orphan_vault_blobs(created_before=blob_cutoff):
        md5 = str(blob.get("md5") or "")
        stored = str(blob.get("stored_path") or "")
        if cal.delete_vault_blob(md5=md5):
            blobs_removed += 1
            if stored:
                path = root / stored
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("failed to delete blob file %s: %s", path, exc)

    return {"links_removed": links_removed, "blobs_removed": blobs_removed}


async def start_background() -> None:
    while True:
        try:
            stats = run_vault_cleanup_once()
            if stats["links_removed"] or stats["blobs_removed"]:
                log.info("vault cleanup: %s", stats)
        except Exception as exc:
            log.warning("vault cleanup error: %s", exc)
        await asyncio.sleep(_CLEANUP_INTERVAL_SEC)
