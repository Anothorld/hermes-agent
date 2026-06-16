"""Email-discover queue holds semaphore until gateway run drains."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_asyncio")

from app.run_launch_queue import RunLaunchQueue


@pytest.mark.asyncio
async def test_drain_email_discover_run_skips_pending(monkeypatch) -> None:
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
    queue = RunLaunchQueue()
    await queue._drain_email_discover_run({"run_id": "pending:abc"})
    assert calls == []


@pytest.mark.asyncio
async def test_drain_email_discover_run_waits_for_sse(monkeypatch) -> None:
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
    queue = RunLaunchQueue()
    await queue._drain_email_discover_run({"run_id": "run-discover-9"})
    assert calls == ["run-discover-9"]
