"""Auto-retry must wire async launch callbacks like operator /rediscover."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.discovery_gate import _trigger_rediscover_internal
from app.gateway_client import GatewayClient


@pytest.mark.asyncio
async def test_auto_retry_async_accept_wires_success_callback(
    tmp_path,
) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (
            sku TEXT PRIMARY KEY, name TEXT, url TEXT, tags_json TEXT,
            notes TEXT, pitch_md TEXT, selling_points TEXT, variants_json TEXT
        );
        CREATE TABLE product_campaigns (
            sku TEXT, campaign_id TEXT, env TEXT, run_id TEXT, status TEXT,
            started_at TEXT, test_mode_to TEXT, target_floor INTEGER,
            baseline_candidate_count INTEGER, retry_count INTEGER,
            floor_unmet_reason TEXT, gate_run_id TEXT,
            diagnostics_history TEXT DEFAULT '[]',
            PRIMARY KEY (campaign_id, env)
        );
        CREATE TABLE product_campaign_runs (
            campaign_id TEXT, env TEXT, run_id TEXT, kind TEXT,
            session_id TEXT, dedup_key TEXT, started_at TEXT, ended_at TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER, action TEXT, target TEXT,
            payload_json TEXT, ts TEXT NOT NULL
        );
        INSERT INTO products VALUES (
            'SKU1', 'P', '', '[]', '', '', '', '[]'
        );
        INSERT INTO product_campaigns VALUES (
            'SKU1', 'CID1', 'LIVE', 'run-old', 'running', '2026-01-01T00:00:00+00:00',
            NULL, 10, 2, 0, NULL, 'run-old', '[]'
        );
        """
    )
    product = conn.execute("SELECT * FROM products WHERE sku='SKU1'").fetchone()
    bridge = AsyncMock()
    bridge.list_candidates = AsyncMock(return_value=[])
    gateway = AsyncMock(spec=GatewayClient)

    captured: dict[str, object] = {}

    async def _fake_launch_or_accept(*_args, **kwargs):
        captured["on_success"] = kwargs.get("on_success")
        captured["on_error"] = kwargs.get("on_error")
        return True, {"job_id": "job1", "status": "accepted"}

    with (
        patch("app.discovery_gate.queue_would_block", return_value=True),
        patch("app.discovery_gate.get_settings") as mock_settings,
        patch("app.discovery_gate.launch_or_accept", side_effect=_fake_launch_or_accept),
        patch("app.discovery_gate.ensure_gateway_bridge_key"),
        patch("app.discovery_gate.materialize_discovery_nox_config", return_value=None),
        patch("app.discovery_gate.learned_criteria_brief_section", return_value=""),
        patch("app.discovery_gate.prior_sku_approved_handles", return_value=[]),
    ):
        mock_settings.return_value.launch_http_202 = True
        out = await _trigger_rediscover_internal(
            bridge=bridge,
            gateway=gateway,
            conn=conn,
            product=product,
            campaign_id="CID1",
            env="LIVE",
            additional_count=5,
            test_mode_to_override=None,
            current_test_mode_to=None,
            rediscovery_instructions="instr",
            actor=None,
            is_auto_retry=True,
            new_retry_count=1,
        )

    assert out.get("accepted") is True
    assert captured["on_success"] is not None
    assert captured["on_error"] is not None
    conn.close()
