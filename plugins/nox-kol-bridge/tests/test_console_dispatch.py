"""Console dispatch claim signing and verification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.campaign_gate import assert_live_allowed  # noqa: E402
from internal.console_dispatch import (  # noqa: E402
    attach_console_dispatch,
    issue_console_dispatch,
    verify_console_dispatch,
)
from internal.exceptions import NoxCampaignGateError  # noqa: E402


@pytest.fixture(autouse=True)
def _dispatch_enforced(monkeypatch):
    """This module tests real dispatch verification (not the global skip)."""
    monkeypatch.setenv("NOX_CONSOLE_DISPATCH_SECRET", "unit-test-dispatch-secret")
    monkeypatch.delenv("NOX_SKIP_CONSOLE_DISPATCH", raising=False)


def test_issue_and_verify_dispatch():
    cfg = {"nox_quota_enabled": True, "campaign_id": "camp-1"}
    attach_console_dispatch(
        cfg,
        campaign_id="camp-1",
        allowed_gates=["shortlist_confirm"],
    )
    verify_console_dispatch(
        cfg,
        gate="shortlist_confirm",
        operation="diligence_pack",
    )
    assert_live_allowed(
        "LIVE",
        cfg,
        operation="diligence_pack",
        gate="shortlist_confirm",
    )


def test_wrong_gate_rejected():
    cfg = {
        "nox_quota_enabled": True,
        "campaign_id": "camp-1",
        "nox_console_dispatch": issue_console_dispatch(
            campaign_id="camp-1",
            allowed_gates=["pre_outreach_confirm"],
        ),
    }
    with pytest.raises(NoxCampaignGateError, match="not in console dispatch"):
        verify_console_dispatch(
            cfg,
            gate="shortlist_confirm",
            operation="diligence_pack",
        )


def test_expired_dispatch_rejected(monkeypatch):
    cfg = {"nox_quota_enabled": True, "campaign_id": "camp-1"}
    attach_console_dispatch(
        cfg,
        campaign_id="camp-1",
        allowed_gates=["shortlist_confirm"],
        ttl_seconds=1,
    )
    import time

    time.sleep(1.1)
    with pytest.raises(NoxCampaignGateError, match="expired"):
        verify_console_dispatch(
            cfg,
            gate="shortlist_confirm",
            operation="diligence_pack",
        )


def test_missing_dispatch_blocks_live():
    cfg = {"nox_quota_enabled": True, "campaign_id": "camp-1"}
    with pytest.raises(NoxCampaignGateError, match="nox_console_dispatch"):
        assert_live_allowed(
            "LIVE",
            cfg,
            operation="diligence_pack",
            gate="shortlist_confirm",
        )
