"""Queue bypass still serializes email discover when launch queue disabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")

from app.run_launch_queue import RunLaunchQueue, drain_email_discover_run


@pytest.mark.asyncio
async def test_drain_email_discover_run_module_helper(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeGateway:
        async def drain_run_events(self, run_id: str) -> None:
            calls.append(run_id)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        "app.gateway_client.GatewayClient",
        lambda: _FakeGateway(),
    )
    await drain_email_discover_run({"run_id": "run-7"})
    assert calls == ["run-7"]


@pytest.mark.asyncio
async def test_launch_bypass_still_holds_email_discover(monkeypatch) -> None:
    settings = MagicMock()
    settings.gateway_launch_queue_enabled = False
    monkeypatch.setattr("app.run_launch_queue.get_settings", lambda: settings)

    drained: list[str] = []
    released: list[bool] = []

    async def _fake_drain(run: dict) -> None:
        drained.append(str(run.get("run_id")))

    monkeypatch.setattr(
        "app.run_launch_queue.drain_email_discover_run",
        _fake_drain,
    )

    queue = RunLaunchQueue()
    original_release = queue._email_sem.release

    def _track_release() -> None:
        released.append(True)
        original_release()

    monkeypatch.setattr(queue._email_sem, "release", _track_release)

    start_fn = AsyncMock(return_value={"run_id": "run-bypass-1"})
    result = await queue.launch(
        start_fn,
        session_id="kol-email-discover:LIVE:9:tok",
        kind="email_discover",
    )

    assert result.run["run_id"] == "run-bypass-1"
    start_fn.assert_awaited_once()
    assert drained == ["run-bypass-1"]
    assert released == [True]
