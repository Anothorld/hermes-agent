"""Tests for discovery skip outcomes (archived last_outcome only)."""

from __future__ import annotations

import pytest


def _seed_identity(cal, handle: str = "skip_me", *, env: str = "LIVE") -> int:
    iid = cal.upsert_identity(primary_handle=handle, platform="instagram", env=env)
    assert iid is not None
    return int(iid)


def _archive_with_outcome(cal, iid: int, outcome: str) -> None:
    cal.upsert_relationship(
        identity_id=iid,
        last_outcome=outcome,
        increment_collabs=True,
        last_campaign_id="C-arch",
    )


@pytest.mark.parametrize("outcome", ["competitor", "success", "aborted", "legacy_collab"])
def test_upsert_candidate_blocked_for_skip_outcomes(cal_db, bridge_pkg, outcome):
    cal = cal_db
    ds = bridge_pkg.discovery_skip
    iid = _seed_identity(cal, f"kol_{outcome}")
    _archive_with_outcome(cal, iid, outcome)
    cal.upsert_campaign_config(campaign_id="C-disc", env="LIVE")
    with pytest.raises(ds.DiscoverySkipActive) as exc:
        cal.upsert_candidate(
            campaign_id="C-disc",
            identity_id=iid,
            source="discovery",
            env="LIVE",
        )
    assert exc.value.reason == outcome


def test_list_discovery_skip_handles_includes_archived_outcomes(cal_db, bridge_pkg):
    cal = cal_db
    ds = bridge_pkg.discovery_skip
    iid = _seed_identity(cal, "archived_success")
    _archive_with_outcome(cal, iid, "success")
    items = cal.list_discovery_skip_handles(env="LIVE")
    by_handle = {row["handle"]: row["reason"] for row in items}
    assert by_handle["archived_success"] == "success"
    assert ds.is_discovery_skip_outcome("success") is True
    assert ds.is_discovery_skip_outcome("declined") is False


def test_allowlist_only_identity_not_skipped(cal_db, bridge_pkg, monkeypatch):
    cal = cal_db
    ds = bridge_pkg.discovery_skip
    pta = bridge_pkg.prior_touch_allowlist
    iid = _seed_identity(cal, "legacy_sheet_only")
    cal.upsert_campaign_config(campaign_id="C-disc", env="LIVE")
    monkeypatch.setattr(
        pta,
        "is_prior_touch_allowlisted",
        lambda **_: True,
    )
    assert ds.resolve_discovery_skip_reason(identity_id=iid, env="LIVE") is None
    candidate_id = cal.upsert_candidate(
        campaign_id="C-disc",
        identity_id=iid,
        source="discovery",
        env="LIVE",
        enforce_outreach_cooldown=False,
    )
    assert candidate_id is not None
