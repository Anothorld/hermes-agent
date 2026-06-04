"""Layer C invariant: ``cal.write_facts`` rejects ``approval.reply_draft``
writes whose embedded ``draft`` envelope is missing or empty in any of
``subject``/``body``/``to`` — failing fast on the writer (skill or
dispatcher) instead of at approve time.

Other ``approval.*`` keys are untouched: the validator is opt-in per
key, so writes like ``approval.foo`` (scalar) and ``approval.bar``
(dict without a ``draft`` slot) continue to round-trip unchanged.
"""

from __future__ import annotations

import pytest


def _write_reply_draft(cal_db, *, identity_id: int, campaign_id: str,
                       draft: dict) -> None:
    cal_db.write_facts(
        identity_id=identity_id, campaign_id=campaign_id,
        namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "pending",
            "source_message_id": "M-x",
            "primary_lane": "commerce",
            "primary_goal": "compensation_negotiation",
            "child_skill": "kol-compensation-negotiator",
            "draft": draft,
        }},
        source="dispatcher", env="TEST",
    )


def test_write_facts_rejects_sparse_reply_draft(cal_db):
    iid = cal_db.upsert_identity(primary_handle="vc1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V1", env="TEST",
                                  test_mode_to="t@x.com")
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        _write_reply_draft(cal_db, identity_id=iid, campaign_id="V1",
                            draft={"body": "hi.", "thread_id": "TH"})
    msg = str(exc_info.value)
    assert "subject" in msg
    assert "to" in msg


def test_write_facts_rejects_empty_string_subject(cal_db):
    iid = cal_db.upsert_identity(primary_handle="vc2", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V2", env="TEST",
                                  test_mode_to="t@x.com")
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        _write_reply_draft(cal_db, identity_id=iid, campaign_id="V2",
                            draft={"subject": "   ",
                                   "body": "hi.",
                                   "to": "k@x.com"})
    assert "subject" in str(exc_info.value)


def test_write_facts_rejects_missing_draft_object(cal_db):
    iid = cal_db.upsert_identity(primary_handle="vc3", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V3", env="TEST",
                                  test_mode_to="t@x.com")
    with pytest.raises(cal_db.FactNamespaceError):
        cal_db.write_facts(
            identity_id=iid, campaign_id="V3",
            namespace="approval",
            facts={"approval.reply_draft": {"decision": "pending"}},
            source="dispatcher", env="TEST",
        )


def test_write_facts_rejects_missing_thread_anchor(cal_db):
    iid = cal_db.upsert_identity(primary_handle="vc6", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V6", env="TEST",
                                  test_mode_to="t@x.com")
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid, campaign_id="V6", namespace="approval",
            facts={"approval.reply_draft": {
                "decision": "pending",
                "primary_lane": "commerce",
                "primary_goal": "product_selection",
                "child_skill": "kol-reply-synthesizer",
                "draft": {
                    "subject": "Re: x",
                    "body": "y",
                    "to": "k@x.com",
                },
            }},
            source="skill:kol-reply-synthesizer", env="TEST",
        )
    assert "thread anchor" in str(exc_info.value).lower()


def test_write_facts_accepts_top_level_thread_anchors(cal_db):
    """techjoyce incident shape: anchors on fact root, not inside draft."""
    iid = cal_db.upsert_identity(primary_handle="vc7", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V7", env="TEST",
                                  test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid, campaign_id="V7", namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "pending",
            "source_message_id": "19e84b2d4cf91067",
            "primary_lane": "commerce",
            "primary_goal": "product_selection",
            "child_skill": "kol-reply-synthesizer",
            "thread_id": "19e81ff6def3b65f",
            "in_reply_to": "19e84b2d4cf91067",
            "draft": {
                "subject": "Re: collab",
                "body": "Thanks for following up.",
                "to": "manager@agency.com",
            },
        }},
        source="draft:19e8ef255f17f7af", env="TEST",
    )
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="V7", env="TEST",
    ).get("approval.reply_draft")
    assert latest["thread_id"] == "19e81ff6def3b65f"


def test_write_facts_accepts_complete_reply_draft(cal_db):
    iid = cal_db.upsert_identity(primary_handle="vc4", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V4", env="TEST",
                                  test_mode_to="t@x.com")
    _write_reply_draft(cal_db, identity_id=iid, campaign_id="V4",
                        draft={"subject": "Re: x",
                               "body": "y",
                               "to": "k@x.com",
                               "thread_id": "TH"})
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="V4", env="TEST",
    ).get("approval.reply_draft")
    assert isinstance(latest, dict)
    assert latest["draft"]["subject"] == "Re: x"
    assert latest["draft"]["to"] == "k@x.com"


def test_other_approval_keys_unaffected(cal_db):
    """Regression guard: scalar / dict-without-draft values under other
    approval.* keys must continue to round-trip — the validator is per-key
    and only fires on approval.reply_draft.
    """
    iid = cal_db.upsert_identity(primary_handle="vc5", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="V5", env="TEST",
                                  test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid, campaign_id="V5", namespace="approval",
        facts={
            "approval.foo": "scalar_value",
            "approval.bar": {"amount": 1500, "currency": "USD"},
        },
        source="resumer", env="TEST",
    )
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="V5", env="TEST",
    )
    assert latest["approval.foo"] == "scalar_value"
    assert latest["approval.bar"]["amount"] == 1500
