"""Phase C fix-7 test — /lanes response carries handle, repeat_count,
last_outcome, archived, and {pending_approvals, open_escalations} counts.

We test the bridge ``cal`` data layer rather than the FastAPI route
directly to avoid a TestClient lift; the route is a thin shim that just
calls these helpers.
"""

from __future__ import annotations


CAMPAIGN = "C-lanes"


def _setup(cal):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN, label="Lanes",
        sku_whitelist=["SKU-L"], deliverable_platforms=["instagram"],
        deliverable_count_per_platform=1, paid_ceiling=1000.0,
        contract_required=True,
    )


def test_lanes_inputs_for_kanban(cal_db, monkeypatch):
    cal = cal_db
    _setup(cal)
    # Silence notifier so escalations don't try to reach a webhook.
    import sys, types
    pkg = sys.modules["kol_ops_bridge_pkg"]
    fake = types.ModuleType("kol_ops_bridge_pkg.notifier")
    fake.notify = lambda **_k: {"sent": True}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kol_ops_bridge_pkg.notifier", fake)
    monkeypatch.setattr(pkg, "notifier", fake, raising=False)

    # KOL A: fresh prospect with one approval pending.
    a = cal.upsert_identity(primary_handle="lanes_a")
    cal.upsert_candidate(campaign_id=CAMPAIGN, identity_id=a, source="discovery")
    cal.write_facts(
        identity_id=a, campaign_id=CAMPAIGN, namespace="approval",
        facts={"approval.over_budget_request": {"amount": 1500}},
    )

    # KOL B: repeat KOL (1 prior successful collab) + active escalation.
    b = cal.upsert_identity(primary_handle="lanes_b")
    cal.archive_collab(identity_id=b, campaign_id="prior",
                       outcome="success", preferred_skus=["SKU-L"])
    cal.upsert_candidate(campaign_id=CAMPAIGN, identity_id=b, source="discovery")
    cal.resolve_candidate_relationships(campaign_id=CAMPAIGN)
    cal.write_facts(
        identity_id=b, campaign_id=CAMPAIGN, namespace="offer",
        facts={"offer.outreach_sent": True, "offer.interest_signal": "confirmed"},
    )
    eid = cal.open_escalation(
        identity_id=b, campaign_id=CAMPAIGN, goal="product_selection",
        reason="kol_demands_off_whitelist",
    )
    assert eid is not None

    # Lane snapshots: identity is queried per candidate; repeat_count and
    # last_outcome flow from kol_relationship.
    cands = cal.list_candidates(CAMPAIGN)
    by_id = {c["identity_id"]: c for c in cands}
    assert by_id[a]["relationship_status"] == "new_prospect"
    assert by_id[b]["relationship_status"] == "repeat_kol"

    rel_a = cal.get_relationship(a) or {}
    rel_b = cal.get_relationship(b) or {}
    assert (rel_a.get("total_collabs") or 0) == 0
    assert rel_b["total_collabs"] == 1
    assert rel_b["last_outcome"] == "success"

    ident_a = cal.get_identity(a)
    assert ident_a["primary_handle"] == "lanes_a"

    # Counts scoped to this campaign.
    pending = [p for p in cal.list_pending_approvals(env="LIVE")
               if p["campaign_id"] == CAMPAIGN]
    assert len(pending) == 1
    open_esc = [e for e in cal.list_escalations(state="awaiting_answer", env="LIVE")
                if e["campaign_id"] == CAMPAIGN]
    assert len(open_esc) == 1

    # Lane buckets are populated per goal_state row.
    lanes_b = cal.get_lanes_view(identity_id=b, campaign_id=CAMPAIGN)
    assert any(s["goal"] == "product_selection" and s["status"] == "blocked"
               for s in lanes_b["commerce"])
