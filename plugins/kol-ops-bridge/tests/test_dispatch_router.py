"""Tests for deterministic per-turn lane routing (select_next_skill)."""

from __future__ import annotations


def _dr(bridge_pkg):
    return bridge_pkg.dispatch_router


def _goal(status, lane, **over):
    row = {"status": status, "lane": lane}
    row.update(over)
    return row


def test_single_commerce_lane(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {
            "deliverables_scope": _goal("active", "commerce"),
            "logistics": _goal("inactive", "fulfillment"),
        },
    })
    assert out["primary_lane"] == "commerce"
    assert out["primary_goal"] == "deliverables_scope"
    assert out["primary_skill"] == "kol-deliverables-clarifier"
    assert out["side_topics"] == []


def test_commerce_beats_fulfillment_by_default(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {
            "compensation_negotiation": _goal("active", "commerce"),
            "logistics": _goal("active", "fulfillment"),
        },
        "facts": {"fulfillment.address_collected": True},
    })
    assert out["primary_lane"] == "commerce"
    assert out["primary_skill"] == "kol-compensation-negotiator"
    # fulfillment demoted to a side-topic
    assert any(t.startswith("fulfillment:logistics") for t in out["side_topics"])
    assert out["severity_reversal_applied"] is False


def test_severity_reversal_promotes_fulfillment(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {
            "compensation_negotiation": _goal("active", "commerce"),
            "logistics": _goal("active", "fulfillment"),
        },
        "facts": {"fulfillment.address_collected": True},
        "signals": [{"name": "not_received", "severity": "critical"}],
    })
    assert out["primary_lane"] == "fulfillment"
    assert out["primary_skill"] == "kol-logistics-tracker"
    assert out["severity_reversal_applied"] is True
    assert any(t.startswith("commerce:") for t in out["side_topics"])


def test_logistics_pre_address_uses_shipping_intake(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {"logistics": _goal("active", "fulfillment")},
        "facts": {},
    })
    assert out["primary_skill"] == "kol-shipping-intake"


def test_blocking_escalation_makes_lane_idle(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {
            "compensation_negotiation": _goal(
                "active", "commerce", blocking_escalation_id=7),
        },
    })
    assert out["primary_skill"] is None
    assert out["primary_lane"] is None


def test_human_gate_triggers_escalate_not_draft(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {
            "product_selection": _goal(
                "active", "commerce", human_gates=["sku_off_whitelist"]),
        },
    })
    assert out["primary_skill"] is None
    assert out["lane_actions"]["commerce"]["action"] == "escalate"


def test_content_production_waits_when_brief_sent_no_draft(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {"content_production": _goal("active", "fulfillment")},
        "facts": {"offer.brief_sent": True},
    })
    # brief sent but no draft → wait, no skill
    assert out["primary_skill"] is None
    assert out["lane_actions"]["fulfillment"]["action"] == "wait"


def test_outreach_path_reengagement(bridge_pkg):
    out = _dr(bridge_pkg).select_next_skill({
        "goals": {"outreach": _goal("active", "commerce")},
        "meta": {"path": "reengagement"},
    })
    assert out["primary_skill"] == "kol-reengagement-outreach"


def test_draftable_plan_multiple_commerce_goals(bridge_pkg):
    out = _dr(bridge_pkg).select_draftable_plan({
        "goals": {
            "product_selection": _goal("active", "commerce"),
            "deliverables_scope": _goal("active", "commerce"),
            "compensation_negotiation": _goal("inactive", "commerce"),
        },
    })
    assert len(out["draftable"]) == 2
    goals = [r["goal"] for r in out["draftable"]]
    assert goals == ["product_selection", "deliverables_scope"]
    assert out["primary_contributor"]["goal"] == "product_selection"


def test_draftable_plan_human_gate_in_escalate(bridge_pkg):
    out = _dr(bridge_pkg).select_draftable_plan({
        "goals": {
            "product_selection": _goal(
                "active", "commerce", human_gates=["sku_off_whitelist"]),
            "deliverables_scope": _goal("active", "commerce"),
        },
    })
    assert len(out["escalate"]) == 1
    assert out["escalate"][0]["goal"] == "product_selection"
    assert len(out["draftable"]) == 1
    assert out["draftable"][0]["goal"] == "deliverables_scope"


def test_draftable_plan_lane_filter(bridge_pkg):
    out = _dr(bridge_pkg).select_draftable_plan({
        "goals": {
            "product_selection": _goal("active", "commerce"),
            "logistics": _goal("active", "fulfillment"),
        },
        "facts": {"fulfillment.address_collected": True},
        "lane_filter": "commerce",
    })
    assert len(out["draftable"]) == 1
    assert out["draftable"][0]["lane"] == "commerce"
