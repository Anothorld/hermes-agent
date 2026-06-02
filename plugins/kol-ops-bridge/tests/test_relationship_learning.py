"""Tests for relationship personalization hints."""

from __future__ import annotations


def test_personalization_hint_repeat_success(cal_db):
    iid = cal_db.upsert_identity(primary_handle="rel1", platform="instagram")
    cal_db.upsert_relationship(
        identity_id=iid,
        last_outcome="success",
        preferred_mode="gifted",
        negotiation_style="soft_anchor",
        increment_collabs=True,
    )
    facts = cal_db.get_reusable_facts(iid)
    assert facts["negotiation_style"] == "soft_anchor"
    assert "Prior collaboration completed successfully" in facts["personalization_hint"]
    assert "gifted" in facts["personalization_hint"]


def test_personalization_hint_empty_for_new_kol(cal_db):
    iid = cal_db.upsert_identity(primary_handle="rel2", platform="instagram")
    facts = cal_db.get_reusable_facts(iid)
    assert facts["personalization_hint"] == ""
    assert facts["negotiation_style"] == "unknown"
