"""In-process async jobs for long-running console operations."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable

_jobs: dict[str, dict[str, Any]] = {}


def create_job(*, kind: str, meta: dict[str, Any] | None = None) -> str:
    """Register a pending job and return its id."""
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "status": "pending",
        "created_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
        "meta": meta or {},
    }
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    row = _jobs.get(job_id)
    return dict(row) if row is not None else None


def find_active_job(
    *,
    kind: str,
    meta_match: dict[str, Any],
) -> str | None:
    """Return an in-flight job id matching ``kind`` and all ``meta_match`` keys."""
    for job_id, row in _jobs.items():
        if row.get("kind") != kind:
            continue
        if row.get("status") not in {"pending", "running"}:
            continue
        meta = row.get("meta") or {}
        if all(meta.get(k) == v for k, v in meta_match.items()):
            return job_id
    return None


def _set_job(job_id: str, **fields: Any) -> None:
    if job_id in _jobs:
        _jobs[job_id].update(fields)


async def run_in_background(
    job_id: str,
    fn: Callable[[], Awaitable[dict[str, Any]]],
) -> None:
    """Execute ``fn`` and store result on the job row."""

    async def _runner() -> None:
        _set_job(job_id, status="running")
        try:
            result = await fn()
            _set_job(
                job_id,
                status="completed",
                result=result,
                finished_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001
            _set_job(
                job_id,
                status="failed",
                error=str(exc),
                finished_at=time.time(),
            )

    asyncio.create_task(_runner())
