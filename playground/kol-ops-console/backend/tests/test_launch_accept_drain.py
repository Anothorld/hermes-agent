"""launch_or_accept must not double-drain email-discover runs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_asyncio")

from app.launch_accept import launch_or_accept


@pytest.mark.asyncio
async def test_accepted_email_discover_skips_background_drain(monkeypatch) -> None:
    settings = MagicMock()
    settings.launch_http_202 = True
    settings.gateway_launch_queue_enabled = True
    monkeypatch.setattr("app.launch_accept.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.launch_accept.queue_would_block",
        lambda **_: True,
    )
    monkeypatch.setattr("app.launch_accept.create_job", lambda **_: "job-1")
    async def _run_inline(_job_id: str, coro):
        return await coro()

    monkeypatch.setattr("app.launch_accept.run_in_background", _run_inline)

    gateway = MagicMock()
    gateway.ensure_run_drained = MagicMock()

    launch_result = MagicMock()
    launch_result.run = {"run_id": "run-ed-1"}
    launch_result.queued = True
    launch_result.waited_sec = 1.0
    launch_result.queue_position = 1

    with patch(
        "app.launch_accept.launch_queue.launch",
        new=AsyncMock(return_value=launch_result),
    ):
        accepted, body = await launch_or_accept(
            gateway,
            AsyncMock(return_value={"run_id": "run-ed-1"}),
            session_id="kol-email-discover:LIVE:42:tok",
            kind="email_discover",
        )

    assert accepted is True
    assert body["job_id"] == "job-1"
    gateway.ensure_run_drained.assert_not_called()


@pytest.mark.asyncio
async def test_accepted_outreach_still_schedules_background_drain(monkeypatch) -> None:
    settings = MagicMock()
    settings.launch_http_202 = True
    settings.gateway_launch_queue_enabled = True
    monkeypatch.setattr("app.launch_accept.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.launch_accept.queue_would_block",
        lambda **_: True,
    )
    monkeypatch.setattr("app.launch_accept.create_job", lambda **_: "job-2")
    async def _run_inline(_job_id: str, coro):
        return await coro()

    monkeypatch.setattr("app.launch_accept.run_in_background", _run_inline)

    gateway = MagicMock()
    gateway.ensure_run_drained = MagicMock()

    launch_result = MagicMock()
    launch_result.run = {"run_id": "run-out-1"}
    launch_result.queued = False
    launch_result.waited_sec = 0.0
    launch_result.queue_position = 0

    with patch(
        "app.launch_accept.launch_queue.launch",
        new=AsyncMock(return_value=launch_result),
    ):
        await launch_or_accept(
            gateway,
            AsyncMock(return_value={"run_id": "run-out-1"}),
            session_id="kol-campaign-outreach:LIVE:CID-1",
            kind="general",
        )

    gateway.ensure_run_drained.assert_called_once_with("run-out-1")
