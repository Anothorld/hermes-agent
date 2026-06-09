"""Integration tests for chase hint + persist supersede."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "plugin_api.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch, cal_db):
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)


def _seed_pending_draft(cal_db, *, iid: int, cid: str, source_message_id: str) -> None:
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "pending",
            "source_message_id": source_message_id,
            "primary_lane": "commerce",
            "primary_goal": "product_selection",
            "child_skill": "kol-reply-synthesizer",
            "draft": {
                "subject": "Re: collab",
                "body": "Old draft body",
                "to": "manager@agency.com",
                "thread_id": "TH1",
            },
        }},
        source=f"draft:{source_message_id}",
        env="TEST",
    )


def test_reply_chase_hint_regenerate(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@k", platform="instagram")
    cid = "C1"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    _seed_pending_draft(cal_db, iid=iid, cid=cid, source_message_id="MSG1")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1"},
    )
    out = cal_db.reply_chase_hint(
        identity_id=iid,
        campaign_id=cid,
        message_id="MSG2",
        thread_id="TH1",
        env="TEST",
    )
    assert out["recommended_action"] == "regenerate"
    assert out["prior_source_message_id"] == "MSG1"


def test_reply_chase_hint_defers_when_escalation_open(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@k_esc", platform="instagram")
    cid = "C-ESC"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    _seed_pending_draft(cal_db, iid=iid, cid=cid, source_message_id="MSG1")
    cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="variant_swap_and_scope_change",
        env="TEST",
        resume_context={"source": "classifier", "source_message_id": "MSG2"},
    )
    out = cal_db.reply_chase_hint(
        identity_id=iid,
        campaign_id=cid,
        message_id="MSG2",
        thread_id="TH1",
        env="TEST",
    )
    assert out["recommended_action"] == "defer_escalation"
    assert out["defer_reason"] == "open_escalation_awaiting_answer"
    assert out["deferred_chase_action"] == "regenerate"


def test_reply_chase_hint_regenerate_after_escalation_resolved(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@k_res", platform="instagram")
    cid = "C-RES"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    _seed_pending_draft(cal_db, iid=iid, cid=cid, source_message_id="MSG1")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
    )
    cal_db.resolve_escalation(
        escalation_id=esc_id,
        decision="resume",
        decided_by="test",
        final_state="resolved",
    )
    out = cal_db.reply_chase_hint(
        identity_id=iid,
        campaign_id=cid,
        message_id="MSG2",
        thread_id="TH1",
        env="TEST",
    )
    assert out["recommended_action"] == "regenerate"


def test_pending_action_blocked_on_chase(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@k2", platform="instagram")
    cid = "C2"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    _seed_pending_draft(cal_db, iid=iid, cid=cid, source_message_id="MSG1")
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=cid,
            namespace="approval",
            facts={
                "approval.pending_action_reply_needed": True,
                "approval.pending_action_reason": "please update draft",
            },
            source="email:MSG2",
            env="TEST",
        )
    assert "pending_action_reply_needed" in str(exc_info.value)


def test_skill_reply_draft_write_blocked(cal_db):
    iid = cal_db.upsert_identity(primary_handle="@k3", platform="instagram")
    cid = "C3"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    with pytest.raises(cal_db.FactNamespaceError) as exc_info:
        cal_db.write_facts(
            identity_id=iid,
            campaign_id=cid,
            namespace="approval",
            facts={"approval.reply_draft": {
                "decision": "pending",
                "source_message_id": "MSG1",
                "primary_lane": "commerce",
                "primary_goal": "product_selection",
                "child_skill": "kol-reply-synthesizer",
                "draft": {
                    "subject": "Re: x",
                    "body": "y",
                    "to": "a@b.com",
                    "thread_id": "TH1",
                },
            }},
            source="skill:kol-reply-synthesizer",
            env="TEST",
        )
    assert "persist-reply-draft" in str(exc_info.value)


def test_persist_blocked_when_escalation_awaiting(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@k_block", platform="instagram")
    cid = "C-BLOCK"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        resume_context={"source": "classifier", "source_message_id": "MSG1"},
    )
    with pytest.raises(plugin_api.HTTPException) as exc_info:
        plugin_api.persist_reply_draft(
            body=plugin_api.PersistReplyDraftBody(
                identity_id=iid,
                campaign_id=cid,
                env="TEST",
                source_message_id="MSG2",
                primary_lane="commerce",
                primary_goal="compensation_negotiation",
                child_skill="kol-reply-synthesizer",
                child_envelope={"body": "Thanks for following up!"},
                latest_email={"from": "a@b.com", "subject": "Re: x", "thread_id": "TH1"},
            ),
            x_bridge_key=None,
        )
    assert exc_info.value.status_code == 409
    assert "open_escalation_awaiting_answer" in str(exc_info.value.detail)


def test_persist_allowed_with_linked_escalation_while_open(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@k_prev", platform="instagram")
    cid = "C-PREV"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        resume_context={"source": "classifier", "source_message_id": "MSG1"},
    )
    out = plugin_api.persist_reply_draft(
        body=plugin_api.PersistReplyDraftBody(
            identity_id=iid,
            campaign_id=cid,
            env="TEST",
            source_message_id="MSG1",
            primary_lane="commerce",
            primary_goal="compensation_negotiation",
            child_skill="kol-compensation-negotiator",
            child_envelope={"body": "Per operator guidance we can offer 5000."},
            latest_email={"from": "a@b.com", "subject": "Re: x", "thread_id": "TH1"},
            linked_escalation_id=esc_id,
        ),
        x_bridge_key=None,
    )
    assert out["ok"] is True
    latest = cal_db.latest_facts_for(identity_id=iid, campaign_id=cid, env="TEST")
    assert latest["approval.reply_draft"]["linked_escalation_id"] == esc_id


def test_persist_blocked_chase_supersede_linked_pending(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@k_link", platform="instagram")
    cid = "C-LINK"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id=cid,
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
        resume_context={"source": "classifier", "source_message_id": "MSG1"},
    )
    cal_db.resolve_escalation(
        escalation_id=esc_id,
        decision="resume",
        decided_by="test",
        final_state="resolved",
    )
    cal_db.write_facts(
        identity_id=iid,
        campaign_id=cid,
        namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "pending",
            "source_message_id": "MSG1",
            "linked_escalation_id": esc_id,
            "primary_lane": "commerce",
            "primary_goal": "compensation_negotiation",
            "child_skill": "kol-compensation-negotiator",
            "draft": {
                "subject": "Re: collab",
                "body": "Resume draft",
                "to": "manager@agency.com",
                "thread_id": "TH1",
            },
        }},
        source=f"draft:MSG1",
        env="TEST",
    )
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1", "from_addr": "a@b.com", "subject": "Re: x"},
    )
    with pytest.raises(plugin_api.HTTPException) as exc_info:
        plugin_api.persist_reply_draft(
            body=plugin_api.PersistReplyDraftBody(
                identity_id=iid,
                campaign_id=cid,
                env="TEST",
                source_message_id="MSG2",
                primary_lane="commerce",
                primary_goal="compensation_negotiation",
                child_skill="kol-reply-synthesizer",
                child_envelope={"body": "Chase ack"},
                latest_email={"from": "a@b.com", "subject": "Re: x", "thread_id": "TH1"},
            ),
            x_bridge_key=None,
        )
    assert exc_info.value.status_code == 409
    assert "linked_escalation_draft_pending" in str(exc_info.value.detail)


def test_persist_supersedes_prior_draft(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="@k4", platform="instagram")
    cid = "C4"
    cal_db.upsert_campaign_config(campaign_id=cid, env="TEST", test_mode_to="t@x.com")
    _seed_pending_draft(cal_db, iid=iid, cid=cid, source_message_id="MSG1")
    cal_db.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="test",
        env="TEST",
        payload={"message_id": "MSG1", "thread_id": "TH1", "from_addr": "a@b.com", "subject": "Re: x"},
    )
    out = plugin_api.persist_reply_draft(
        body=plugin_api.PersistReplyDraftBody(
            identity_id=iid,
            campaign_id=cid,
            env="TEST",
            source_message_id="MSG2",
            primary_lane="commerce",
            primary_goal="product_selection",
            child_skill="kol-reply-synthesizer",
            child_envelope={"body": "Thanks for following up!"},
            latest_email={
                "from": "a@b.com",
                "subject": "Re: x",
                "thread_id": "TH1",
            },
        ),
        x_bridge_key=None,
    )
    assert out["chase_superseded"] is True
    assert out["prior_source_message_id"] == "MSG1"
    latest = cal_db.latest_facts_for(identity_id=iid, campaign_id=cid, env="TEST")
    draft = latest["approval.reply_draft"]
    assert draft["source_message_id"] == "MSG2"
    assert draft["chase_supersede"]["prior_source_message_id"] == "MSG1"
