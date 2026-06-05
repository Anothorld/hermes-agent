"""Per-dimension diligence cache reuse across discovery and Gate A."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal import commands  # noqa: E402
from internal.campaign_gate import assert_live_allowed  # noqa: E402
from internal.console_dispatch import attach_console_dispatch  # noqa: E402
from internal.nox_cache import current_cache_month  # noqa: E402


@pytest.fixture(autouse=True)
def _dispatch_enforced(monkeypatch):
    monkeypatch.setenv("NOX_CONSOLE_DISPATCH_SECRET", "unit-test-dispatch-secret")
    monkeypatch.delenv("NOX_SKIP_CONSOLE_DISPATCH", raising=False)


def _cfg() -> dict:
    out = {"nox_quota_enabled": True, "campaign_id": "camp-1"}
    attach_console_dispatch(
        out,
        campaign_id="camp-1",
        allowed_gates=["discovery_qualify", "shortlist_confirm"],
    )
    return out


def test_discovery_qualify_gate_accepted():
    cfg = _cfg()
    verify = __import__(
        "internal.console_dispatch", fromlist=["verify_console_dispatch"]
    ).verify_console_dispatch
    verify(cfg, gate="discovery_qualify", operation="diligence_pack")
    assert_live_allowed(
        "LIVE",
        cfg,
        operation="diligence_pack",
        gate="discovery_qualify",
    )


def test_audience_cached_then_gate_a_fetches_only_missing(monkeypatch):
    cfg = _cfg()
    tz = "UTC"
    month = current_cache_month(tz)
    creator_id = "ig_creator_partial_cache"

    audience_fixture = {
        "audience": commands.cli_runner.load_fixture("diligence_pack.json")["audience"]
    }

    calls: list[str] = []

    def _fake_read(env, lang, dimension, *args, **kwargs):
        calls.append(dimension)
        return commands.cli_runner.load_fixture("diligence_pack.json").get(
            dimension,
            {"data": {"creator_id": creator_id}},
        )

    monkeypatch.setattr(commands, "_creator_read", _fake_read)

    commands._store_diligence_bundle(
        month, creator_id, ["audience"], "en", audience_fixture
    )

    out = commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=100,
        tz_name=tz,
        lang="en",
        nox_creator_id=creator_id,
        platform=None,
        url=None,
        channel_id=None,
        dimensions=["profile", "audience", "content"],
        include_cooperation=False,
        campaign_config=cfg,
    )

    assert out["cache_hit"] is False
    assert out["api_calls"] == 2
    assert "audience" not in calls
    assert set(calls) == {"profile", "content"}
    assert out.get("cache_key", "").startswith("diligence_pack|")
