"""Batch list loaders (approvals handles, facts subset, escalations filter)."""

from __future__ import annotations


def _seed_identity_and_pending_approval(cal, *, handle: str, campaign_id: str) -> int:
    cal.upsert_campaign_config(campaign_id=campaign_id, env="LIVE")
    slug = handle.lstrip("@")
    iid = cal.upsert_identity(
        primary_handle=handle,
        primary_email=f"{slug}@example.com",
        env="LIVE",
    )
    cal.write_facts(
        identity_id=iid,
        campaign_id=campaign_id,
        namespace="approval",
        facts={
            "approval.reply_draft": {
                "decision": "pending",
                "draft": {"subject": "Hi", "body": "Hello", "to": f"{slug}@example.com"},
            },
        },
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
