"""Batch list loaders (approvals handles, facts subset, escalations filter)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed_identity_and_pending_approval(cal, *, handle: str, campaign_id: str) -> int:
    cal.upsert_campaign_config(campaign_id=campaign_id, env="LIVE")
    slug = handle.lstrip("@")
    iid = cal.upsert_identity(
        primary_handle=handle,
        primary_email=f"{slug}@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=campaign_id, identity_id=iid, source="test", env="LIVE",
    )
    cal.select_candidates_for_outreach(
        campaign_id=campaign_id,
        identity_ids=[iid],
        selected_by="test",
        env="LIVE",
    )
    cal.write_facts(
        identity_id=iid,
        campaign_id=campaign_id,
        namespace="approval",
        facts={"approval.over_budget_request": {"amount": 1500, "decision": "pending"}},
        source="test",
        env="LIVE",
    )
    return iid


def test_pending_approvals_include_handle(bridge_pkg, cal_db):
    cal = cal_db
    cid = "APPR-HANDLE-1"
    iid = _seed_identity_and_pending_approval(cal, handle="@slow_kol", campaign_id=cid)
    rows = cal.list_pending_approvals(env="LIVE", campaign_id=cid)
    assert len(rows) >= 1
    match = next(r for r in rows if r["identity_id"] == iid)
    assert match["handle"] == "slow_kol"


def test_batch_nox_facts_subset(bridge_pkg, cal_db):
    cal = cal_db
    cid = "NOX-BATCH-1"
    iid = cal.upsert_identity(primary_handle="@nox1", primary_email="n1@example.com", env="LIVE")
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    cal.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="identity",
        facts={"identity.nox_creator_id": "creator-99"},
        source="test",
        env="LIVE",
    )
    by_id = cal.batch_latest_facts_subset(
        campaign_id=cid,
        identity_ids=[iid],
        env="LIVE",
        fact_keys=cal.SHORTLIST_NOX_FACT_KEYS,
    )
    assert by_id[iid]["identity.nox_creator_id"] == "creator-99"


def test_get_lanes_excludes_discovered_even_with_stale_selected_at(bridge_pkg, cal_db):
    """Stale selected_at on discovered rows must not leak into kanban."""
    cal = cal_db
    plugin_api = _load_plugin_api()
    cid = "LANES-STALE-SELECTED-AT"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    stale_id = cal.upsert_identity(
        primary_handle="@stale_selected",
        primary_email="stale@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid,
        identity_id=stale_id,
        source="test",
        candidate_status="discovered",
        env="LIVE",
    )
    cal.select_candidates_for_outreach(
        campaign_id=cid,
        identity_ids=[stale_id],
        selected_by="test",
        env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid,
        identity_ids=[stale_id],
        candidate_status="discovered",
        env="LIVE",
    )
    with cal_db._connect() as conn:
        conn.execute(
            """UPDATE campaign_candidates
                  SET selected_at=?, selected_by=?
                WHERE campaign_id=? AND env=? AND identity_id=?""",
            ("2026-01-01T00:00:00+00:00", "stale:operator", cid, "LIVE", stale_id),
        )
    payload = plugin_api.get_lanes(cid, env="LIVE")
    assert payload["items"] == []


def test_get_lanes_excludes_discovery_pool_candidates(bridge_pkg, cal_db):
    """Kanban must not list discovered/shortlisted rows — only approved."""
    cal = cal_db
    plugin_api = _load_plugin_api()
    cid = "LANES-EXCLUDE-DISCOVERY"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    discovered_id = cal.upsert_identity(
        primary_handle="@pool_only",
        primary_email="pool@example.com",
        env="LIVE",
    )
    approved_id = cal.upsert_identity(
        primary_handle="@approved_kol",
        primary_email="approved@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid,
        identity_id=discovered_id,
        source="test",
        candidate_status="discovered",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid,
        identity_id=approved_id,
        source="test",
        candidate_status="selected_for_outreach",
        env="LIVE",
    )
    cal.select_candidates_for_outreach(
        campaign_id=cid,
        identity_ids=[approved_id],
        selected_by="test",
        env="LIVE",
    )
    payload = plugin_api.get_lanes(cid, env="LIVE")
    handles = {item["handle"] for item in payload["items"]}
    assert handles == {"approved_kol"}


def test_get_lanes_pending_approvals_scoped_to_campaign(bridge_pkg, cal_db):
    """Kanban lanes must not scan every campaign's pending approvals."""
    cal = cal_db
    plugin_api = _load_plugin_api()
    hot_cid = "LANES-SCOPE-HOT"
    other_cid = "LANES-SCOPE-OTHER"
    _seed_identity_and_pending_approval(cal, handle="@hot_kol", campaign_id=hot_cid)
    for i in range(12):
        _seed_identity_and_pending_approval(
            cal, handle=f"@noise_{i}", campaign_id=other_cid,
        )
    payload = plugin_api.get_lanes(hot_cid, env="LIVE")
    assert payload["counts"]["pending_approvals"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["handle"] == "hot_kol"


def test_list_escalations_filter_by_identity(bridge_pkg, cal_db):
    cal = cal_db
    cid = "ESC-FILTER-1"
    iid_a = cal.upsert_identity(primary_handle="@a", primary_email="a@example.com", env="LIVE")
    iid_b = cal.upsert_identity(primary_handle="@b", primary_email="b@example.com", env="LIVE")
    cal.open_escalation(
        identity_id=iid_a,
        campaign_id=cid,
        goal="interest_qualification",
        reason="test_a",
        env="LIVE",
    )
    cal.open_escalation(
        identity_id=iid_b,
        campaign_id=cid,
        goal="interest_qualification",
        reason="test_b",
        env="LIVE",
    )
    rows = cal.list_escalations(
        state="awaiting_answer", env="LIVE", identity_id=iid_a, campaign_id=cid,
    )
    assert len(rows) == 1
    assert rows[0]["identity_id"] == iid_a
    assert rows[0]["handle"] == "a"
