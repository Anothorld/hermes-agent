"""Discovery → outreach router (scoped vs full-pool)."""

from __future__ import annotations


def _seed_discovered(cal, *, campaign_id: str, handle: str) -> int:
    cal.upsert_campaign_config(campaign_id=campaign_id, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle=handle,
        primary_email=f"{handle.lstrip('@')}@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=iid,
        source="test",
        candidate_status="discovered",
        env="LIVE",
    )
    return iid


def test_route_discovery_scoped_selects_only_requested_ids(bridge_pkg, cal_db):
    """Console approve-shortlist passes identity_ids so unchecked KOLs stay discovered."""
    cal = cal_db
    router = bridge_pkg.discovery_router  # type: ignore[attr-defined]
    cid = "ROUTE-SCOPE-1"
    a = _seed_discovered(cal, campaign_id=cid, handle="@kol_a")
    b = _seed_discovered(cal, campaign_id=cid, handle="@kol_b")
    c = _seed_discovered(cal, campaign_id=cid, handle="@kol_c")

    out = router.route_discovery_pool(
        campaign_id=cid,
        env="LIVE",
        selected_by="web:operator@test",
        identity_ids=[b],
    )

    assert out["scoped_identity_ids"] == [b]
    assert out["routed_to_cold"] == [b]
    assert out["skipped_already_routed"] == []

    rows = {r["identity_id"]: r["candidate_status"] for r in cal.list_candidates(cid, env="LIVE")}
    assert rows[a] == "discovered"
    assert rows[b] == "selected_for_outreach"
    assert rows[c] == "discovered"


def test_route_discovery_full_pool_when_identity_ids_omitted(bridge_pkg, cal_db):
    """Agent route-discovery without identity_ids still routes the whole discovered pool."""
    cal = cal_db
    router = bridge_pkg.discovery_router  # type: ignore[attr-defined]
    cid = "ROUTE-FULL-1"
    a = _seed_discovered(cal, campaign_id=cid, handle="@full_a")
    b = _seed_discovered(cal, campaign_id=cid, handle="@full_b")

    out = router.route_discovery_pool(
        campaign_id=cid,
        env="LIVE",
        selected_by="agent",
    )

    assert out["scoped_identity_ids"] is None
    assert set(out["routed_to_cold"]) == {a, b}
    rows = {r["identity_id"]: r["candidate_status"] for r in cal.list_candidates(cid, env="LIVE")}
    assert rows[a] == "selected_for_outreach"
    assert rows[b] == "selected_for_outreach"
