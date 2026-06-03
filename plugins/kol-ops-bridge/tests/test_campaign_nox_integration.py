"""CAL persistence for Nox integration knobs on campaign_config."""

from __future__ import annotations


def test_upsert_and_read_nox_integration(cal_db) -> None:
    cal_db.upsert_campaign_config(
        campaign_id="POVISON-NOX-TEST",
        env="LIVE",
        label="Nox test",
        nox_quota_enabled=True,
        nox_monthly_budget=1200,
        nox_supplement_enabled=False,
    )
    cfg = cal_db.get_campaign_config("POVISON-NOX-TEST", env="LIVE")
    assert cfg is not None
    assert cfg["nox_quota_enabled"] is True
    assert cfg["nox_monthly_budget"] == 1200
    assert cfg["nox_supplement_enabled"] is False
    assert "nox_integration_json" not in cfg


def test_merge_nox_integration_partial_update(cal_db) -> None:
    cal_db.upsert_campaign_config(
        campaign_id="POVISON-NOX-MERGE",
        env="LIVE",
        nox_quota_enabled=True,
        nox_monthly_budget=500,
    )
    cal_db.upsert_campaign_config(
        campaign_id="POVISON-NOX-MERGE",
        env="LIVE",
        nox_monthly_budget=800,
    )
    cfg = cal_db.get_campaign_config("POVISON-NOX-MERGE", env="LIVE")
    assert cfg is not None
    assert cfg["nox_quota_enabled"] is True
    assert cfg["nox_monthly_budget"] == 800
