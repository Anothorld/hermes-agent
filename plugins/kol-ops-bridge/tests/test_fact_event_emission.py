"""Phase A1 — verify ``cal.write_facts`` auto-emits the per-goal
``event_type`` vocabulary listed in plan.md.

Previously every event in ``kol_conversation_events`` had to be
written explicitly by a skill, so the timeline only showed
``outbound_draft_created`` / ``outbound_sent`` and missed the
business-state-transition events (``compensation.kol_quoted``,
``contract.signed``, ``content.posted`` etc.). Now ``write_facts``
emits those automatically when their fact_key flips truthy.
"""

from __future__ import annotations


CAMPAIGN = "C-evt"


def _setup(cal):
    cal.upsert_campaign_config(campaign_id=CAMPAIGN, env="TEST",
                               sku_whitelist=["SKU-A"],
                               deliverable_platforms=["instagram"],
                               deliverable_count_per_platform=1,
                               paid_ceiling=1000.0)
    iid = cal.upsert_identity(primary_handle="evt-kol")
    return iid


def _list_event_types(cal, iid: int) -> list[str]:
    with cal._connect() as conn:
        rows = conn.execute(
            "SELECT event_type FROM kol_conversation_events "
            "WHERE identity_id=? AND campaign_id=? ORDER BY id",
            (iid, CAMPAIGN),
        ).fetchall()
    return [r["event_type"] for r in rows]


def test_outreach_sent_fact_emits_event(cal_db):
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.outreach_sent": True},
                    source="skill:cold-outreach", env="TEST")
    assert "outreach.sent" in _list_event_types(cal, iid)


def test_compensation_kol_quoted_emits_event(cal_db):
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.kol_paid_quote": 1500.0},
                    source="skill:negotiator", env="TEST")
    assert "compensation.kol_quoted" in _list_event_types(cal, iid)


def test_contract_signed_emits_event(cal_db):
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.contract_signed": True},
                    source="skill:contract-coordinator", env="TEST")
    assert "contract.signed" in _list_event_types(cal, iid)


def test_logistics_tracking_filled_emits_event(cal_db):
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN,
                    namespace="fulfillment",
                    facts={"fulfillment.tracking_filled": "1Z999AA10123456784"},
                    source="skill:logistics-tracker", env="TEST")
    assert "logistics.tracking_filled" in _list_event_types(cal, iid)


def test_falsy_fact_does_not_emit_event(cal_db):
    """An ``offer.contract_signed`` write with value ``False`` (e.g. a
    state-machine reset) must NOT pollute the timeline.
    """
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
                    facts={"offer.contract_signed": False},
                    source="skill:contract-coordinator", env="TEST")
    assert "contract.signed" not in _list_event_types(cal, iid)


def test_unmapped_fact_does_not_emit_event(cal_db):
    """Approval facts and arbitrary identity facts shouldn't generate
    auto-events (they have their own dedicated paths).
    """
    cal = cal_db
    iid = _setup(cal)
    cal.write_facts(identity_id=iid, campaign_id=CAMPAIGN,
                    namespace="approval",
                    facts={"approval.shortlist": ["a", "b"]},
                    source="skill:shortlist", env="TEST")
    assert _list_event_types(cal, iid) == []
