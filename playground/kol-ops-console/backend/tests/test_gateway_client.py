"""Tests for gateway start-run retry and concurrency error detection."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("httpx")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.gateway_client import (  # noqa: E402
    GatewayClient,
    GatewayError,
    is_gateway_concurrency_limit,
)
from app.gateway_http import http_exception_from_gateway_start  # noqa: E402


def test_is_gateway_concurrency_limit_matches_openai_error_body():
    exc = GatewayError(
        429,
        '{"error": {"message": "Too many concurrent runs (max 10)", '
        '"type": "invalid_request_error", "code": "rate_limit_exceeded"}}',
    )
    assert is_gateway_concurrency_limit(exc)


def test_http_exception_from_gateway_start_maps_concurrency_to_429():
    exc = GatewayError(429, "Too many concurrent runs (max 10)")
    http_exc = http_exception_from_gateway_start(exc, action_label="生成待审批草稿")
    assert http_exc.status_code == 429
    detail = http_exc.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "gateway_concurrency_limit"
    assert "10" in str(detail.get("message"))


@pytest.mark.asyncio
async def test_start_run_with_retry_waits_for_concurrency_slot(monkeypatch):
    client = GatewayClient.__new__(GatewayClient)
    client.start_run = AsyncMock(
        side_effect=[
            GatewayError(
                429,
                '{"error": {"message": "Too many concurrent runs (max 10)", '
                '"code": "rate_limit_exceeded"}}',
            ),
            {"run_id": "run-ok"},
        ]
    )
    sleeps: list[float] = []

    async def _fake_sleep(sec: float) -> None:
        sleeps.append(sec)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        "app.gateway_client.get_settings",
        lambda: type("S", (), {"gateway_base": "http://x", "gateway_key": "", "gateway_yolo": False})(),
    )

    out = await GatewayClient.start_run_with_retry(
        client,
        input="brief",
        concurrency_retries=2,
        concurrency_retry_delay_sec=0.01,
    )
    assert out["run_id"] == "run-ok"
    assert client.start_run.await_count == 2
    assert sleeps == [0.01]
