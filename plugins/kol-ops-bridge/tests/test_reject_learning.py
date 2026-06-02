"""Integration tests for structured draft-rejection learning."""

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
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch, cal_db):
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)


def _seed_pending_draft(cal_db, plugin_api, *, identity_id: int) -> None:
    plugin_api.persist_reply_draft(
        plugin_api.PersistReplyDraftBody(
            identity_id=identity_id,
            campaign_id="C1",
            env="TEST",
            source_message_id="M1",
            primary_lane="commerce",
            primary_goal="compensation_negotiation",
            child_skill="kol-compensation-negotiator",
            child_envelope={"body": "We can do $1200 cash on top of product."},
            latest_email={"from": "kol@x.com", "subject": "rate", "thread_id": "TH1"},
        ),
        x_bridge_key=None,
    )


def test_reject_writes_learning_event(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="rej1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")
    esc_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id="C1",
        goal="compensation_negotiation",
        reason="test",
        env="TEST",
    )
    _seed_pending_draft(cal_db, plugin_api, identity_id=iid)
    facts = cal_db.latest_facts_for(identity_id=iid, campaign_id="C1", env="TEST")
    draft = facts["approval.reply_draft"]
    draft["linked_escalation_id"] = esc_id
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="approval",
        facts={"approval.reply_draft": draft},
        source="test",
        env="TEST",
    )

    out = plugin_api.reject(
        "approval.reply_draft",
        plugin_api.ApprovalDecisionBody(
            identity_id=iid,
            campaign_id="C1",
            env="TEST",
            decided_by="operator:test",
            correction=plugin_api.ApprovalCorrectionBody(
                tags=["premature_pricing", "too_long"],
                note="Do not mention price yet",
                suggested_fix="Ask deliverables first",
            ),
        ),
        x_bridge_key=None,
    )
    assert out["decision"] == "rejected"
    assert out["learning_event_id"] is not None

    events = cal_db.list_events(env="TEST", identity_id=iid, limit=10)
    learning = [e for e in events if e.get("event_type") == "draft_rejected_learning"]
    assert len(learning) == 1
    payload = learning[0]["payload"]
    assert payload["tags"] == ["premature_pricing", "too_long"]
    assert payload["child_skill"] == "kol-compensation-negotiator"
    assert "agent_body" in payload

    esc = cal_db.get_escalation(esc_id)
    rejected = esc["resume_context"]["rejected_drafts"][-1]
    assert rejected["tags"] == ["premature_pricing", "too_long"]
    assert rejected["suggested_fix"] == "Ask deliverables first"


def test_dispatch_context_includes_learning_hints(cal_db, bridge_pkg):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="hint1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")
    cal_db.recompute_goals(identity_id=iid, campaign_id="C1", env="TEST")
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """UPDATE kol_goal_state SET status='inactive'
               WHERE identity_id=? AND campaign_id=? AND goal='outreach' AND env=?""",
            (iid, "C1", "TEST"),
        )
        conn.execute(
            """UPDATE kol_goal_state SET status='active'
               WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
            (iid, "C1", "compensation_negotiation", "TEST"),
        )
        conn.commit()
        bridge_pkg.policies.put_policy(
            conn,
            scope="reply_learning",
            content_md="## compensation_negotiation\n- Do not open with price.\n## outreach\n- Skip.",
            updated_by="test",
            env="TEST",
        )
    cal_db.write_event(
        identity_id=iid,
        campaign_id="C1",
        event_type="draft_rejected_learning",
        goal="compensation_negotiation",
        lane="commerce",
        actor="test",
        payload={
            "tags": ["premature_pricing"],
            "note": "too early",
            "child_skill": "kol-compensation-negotiator",
            "goal": "compensation_negotiation",
            "agent_body": "Price is $1200",
        },
        env="TEST",
    )
    ctx = plugin_api.get_dispatch_context(
        identity_id=iid, campaign_id="C1", env="TEST",
    )
    assert "learning_hints" in ctx
    assert ctx["learning_hints"]["hints"]
    policy_hints = [h for h in ctx["learning_hints"]["hints"] if h.get("source") == "policy"]
    assert policy_hints
    assert "outreach" not in policy_hints[0]["content"]
    assert ctx["learning_hints"]["active_goals"] == ["compensation_negotiation"]
    assert ctx["reusable_facts"]["facts"].get("personalization_hint") == ""
