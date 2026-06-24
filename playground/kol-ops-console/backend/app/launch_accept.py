"""Non-blocking HTTP accept for gateway launches when the queue is busy."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from .background_jobs import create_job, find_active_job, run_in_background
from .config import get_settings
from .gateway_client import GatewayClient, GatewayError
from .run_launch_queue import LaunchResult, _BROWSER_SERIAL_KINDS, launch_queue

log = logging.getLogger(__name__)

StartFn = Callable[[], Awaitable[dict[str, Any]]]
OnSuccess = Callable[[dict[str, Any], LaunchResult], Awaitable[None]]
OnError = Callable[[Exception], Awaitable[None]]

_LAUNCH_JOB_KIND = "gateway-launch"


def queue_would_block(*, session_id: str, kind: str | None = None) -> bool:
    """True when ``launch_queue.launch`` would wait before starting."""
    if not get_settings().gateway_launch_queue_enabled:
        return False
    resolved = kind or launch_queue._kind_from_session(session_id)
    if resolved in _BROWSER_SERIAL_KINDS:
        return launch_queue.email_discover_busy()
    snap = launch_queue.snapshot()
    if snap["queue_depth"] > 0:
        return True
    general = int(snap["inflight"].get("general", 0))
    return general >= get_settings().gateway_launch_max_inflight


def _accepted_body(
    *,
    job_id: str,
    session_id: str,
    deduped: bool = False,
    pending_run_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "job_id": job_id,
        "status": "accepted",
        "poll": f"/campaigns/launch-jobs/{job_id}",
        "session_id": session_id,
        "queue": launch_queue.snapshot(),
        "deduped": deduped,
    }
    if pending_run_id:
        body["pending_run_id"] = pending_run_id
    return body


async def launch_or_accept(
    gateway: GatewayClient,
    start_fn: StartFn,
    *,
    session_id: str,
    dedup_key: str | None = None,
    kind: str | None = None,
    on_success: OnSuccess | None = None,
    on_error: OnError | None = None,
    job_meta: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Launch immediately or accept async (202-shaped body).

    Returns ``(accepted_async, body)``. When ``accepted_async`` is False,
    ``body`` is the gateway run dict (possibly with ``_queued`` hints).
    """
    settings = get_settings()
    if not (settings.launch_http_202 and queue_would_block(
        session_id=session_id, kind=kind,
    )):
        run = await gateway.launch_via_queue(
            start_fn,
            session_id=session_id,
            dedup_key=dedup_key,
            kind=kind,
        )
        return False, run

    meta = {
        "session_id": session_id,
        "dedup_key": dedup_key,
        "kind": kind or launch_queue._kind_from_session(session_id),
        **(job_meta or {}),
    }

    if dedup_key:
        existing = find_active_job(
            kind=_LAUNCH_JOB_KIND,
            meta_match={"dedup_key": dedup_key},
        )
        if existing:
            from .background_jobs import get_job

            prior = get_job(existing) or {}
            prior_meta = prior.get("meta") if isinstance(prior.get("meta"), dict) else {}
            prior_pending = prior_meta.get("pending_run_id")
            return True, _accepted_body(
                job_id=existing,
                session_id=session_id,
                deduped=True,
                pending_run_id=(
                    str(prior_pending)
                    if isinstance(prior_pending, str) and prior_pending
                    else None
                ),
            )

    job_id = create_job(kind=_LAUNCH_JOB_KIND, meta=meta)

    async def _runner() -> dict[str, Any]:
        try:
            result = await launch_queue.launch(
                start_fn,
                session_id=session_id,
                dedup_key=dedup_key,
                kind=kind,
            )
            run = result.run
            run_id = run.get("run_id") if isinstance(run, dict) else None
            resolved_kind = kind or launch_queue._kind_from_session(session_id)
            if (
                isinstance(run_id, str)
                and run_id
                and resolved_kind not in _BROWSER_SERIAL_KINDS
            ):
                gateway.ensure_run_drained(run_id)
            if on_success is not None:
                await on_success(run, result)
            payload: dict[str, Any] = {
                "ok": True,
                "run_id": run_id,
                "run": run,
            }
            if result.queued:
                payload["queued"] = True
                payload["waited_sec"] = result.waited_sec
                payload["queue_position"] = result.queue_position
            return payload
        except Exception as exc:
            if on_error is not None:
                await on_error(exc)
            raise

    await run_in_background(job_id, _runner)
    meta_pending = (job_meta or {}).get("pending_run_id")
    return True, _accepted_body(
        job_id=job_id,
        session_id=session_id,
        pending_run_id=(
            str(meta_pending)
            if isinstance(meta_pending, str) and meta_pending
            else None
        ),
    )
