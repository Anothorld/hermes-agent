"""Style learning: LLM propose → approval → policy merge."""

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
def _open_mode(monkeypatch, bridge_pkg):
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)


def _insert_edit_event(cal_db, *, identity_id: int, campaign_id: str, env: str = "LIVE") -> None:
    cal = cal_db
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', 'outreach', 'commerce', 'test', datetime('now'), ?, ?)""",
            (
                identity_id,
                campaign_id,
                '{"was_edited": true, "child_skill": "kol-reply-synthesizer", '
                '"edit_distance": 12, "normalized_agent_body": "Hi there", '
                '"normalized_sent_body": "Hello!"}',
                env,
            ),
        )
        conn.commit()


def _propose_one_batch(cal_db, bridge_pkg, *, handle: str, campaign_id: str) -> dict:
    """Insert one edit event and open a pending style proposal."""
    cal = cal_db
    distill = bridge_pkg.learning_distill
    iid = cal.upsert_identity(primary_handle=handle, env="LIVE")
    _insert_edit_event(cal_db, identity_id=iid, campaign_id=campaign_id)
    with cal._connect() as conn:  # type: ignore[attr-defined]
        distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            limit=50,
            batch_size=1,
        )
        pending = distill.find_pending_style_proposal(
            conn, env="LIVE", scope="company_style",
        )
    assert pending is not None
    return pending


def test_propose_style_learning_creates_pending_approval(cal_db, bridge_pkg):
    pending = _propose_one_batch(
        cal_db, bridge_pkg, handle="style_learn_kol", campaign_id="C_STYLE",
    )
    assert pending["value"]["decision"] == "pending"
    assert pending["value"].get("proposed_markdown")


def test_approve_style_proposal_merges_policy(cal_db, bridge_pkg):
    cal = cal_db
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies

    iid = cal.upsert_identity(primary_handle="style_learn_kol2", env="LIVE")
    _insert_edit_event(cal_db, identity_id=iid, campaign_id="C_STYLE2")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            limit=50,
            batch_size=1,
        )
        pending = distill.find_pending_style_proposal(
            conn, env="LIVE", scope="company_style",
        )
        assert pending is not None
        proposal = pending["value"]
        result = distill.apply_approved_style_proposal(
            conn,
            env="LIVE",
            proposal=proposal,
            updated_by="approval:test",
        )
        assert result.get("version", 0) >= 1
        row = pol.get_policy(conn, scope="company_style")
        assert row is not None
        assert "## Approved style learning" in (row.get("content_md") or "")
        assert "Proposed style updates" in (row.get("content_md") or "") or "Edit-pattern" in (
            row.get("content_md") or ""
        )


def test_style_proposal_reject_does_not_open_escalation(cal_db, bridge_pkg):
    plugin_api = _load_plugin_api()
    cal = cal_db
    distill = bridge_pkg.learning_distill
    store = bridge_pkg.learning_store

    iid = cal.upsert_identity(primary_handle="style_reject_kol", env="LIVE")
    _insert_edit_event(cal_db, identity_id=iid, campaign_id="C_REJ")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            limit=50,
            batch_size=1,
        )
        pending = distill.find_pending_style_proposal(
            conn, env="LIVE", scope="company_style",
        )
    assert pending is not None
    anchor_id = int(pending["identity_id"])
    before = len(cal.list_escalations(env="LIVE"))

    out = plugin_api.reject(
        store.STYLE_LEARNING_APPROVAL_FACT,
        plugin_api.ApprovalDecisionBody(
            identity_id=anchor_id,
            campaign_id=pending.get("campaign_id") or "",
            env="LIVE",
            decided_by="operator:test",
            note="batch quality too noisy",
        ),
        x_bridge_key=None,
    )
    assert out["decision"] == "rejected"
    assert out.get("derived_escalation_id") is None

    after = len(cal.list_escalations(env="LIVE"))
    assert after == before

    facts = cal.latest_facts_for(
        identity_id=anchor_id, campaign_id=None, env="LIVE",
    )
    rejected = facts.get(store.STYLE_LEARNING_APPROVAL_FACT)
    assert isinstance(rejected, dict)
    assert rejected.get("decision") == "rejected"
    assert rejected.get("proposed_markdown")
    assert rejected.get("source_event_ids")


def test_approve_style_proposal_via_api_null_campaign_merges_policy(cal_db, bridge_pkg):
    """Console sends campaign_id=null for identity-level style proposals."""
    plugin_api = _load_plugin_api()
    cal = cal_db
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store
    distill = bridge_pkg.learning_distill

    pending = _propose_one_batch(
        cal_db, bridge_pkg, handle="style_api_approve", campaign_id="C_API",
    )
    anchor_id = int(pending["identity_id"])
    source_ids = pending["value"].get("source_event_ids") or []

    out = plugin_api.approve(
        store.STYLE_LEARNING_APPROVAL_FACT,
        plugin_api.ApprovalDecisionBody(
            identity_id=anchor_id,
            campaign_id=None,
            env="LIVE",
            decided_by="operator:test",
        ),
        x_bridge_key=None,
    )
    assert out["decision"] == "approved"
    assert out.get("style_policy_apply", {}).get("version", 0) >= 1

    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = pol.get_policy(conn, scope="company_style")
        consumed = distill.list_consumed_edit_event_ids(conn, env="LIVE")
    assert row is not None
    assert "## Approved style learning" in (row.get("content_md") or "")
    assert source_ids and all(int(i) in consumed for i in source_ids)

    facts = cal.latest_facts_for(
        identity_id=anchor_id, campaign_id=None, env="LIVE",
    )
    approved = facts.get(store.STYLE_LEARNING_APPROVAL_FACT)
    assert isinstance(approved, dict)
    assert approved.get("decision") == "approved"
    assert approved.get("proposed_markdown")
    assert approved.get("style_policy_apply")


def test_approve_style_proposal_idempotent_replay(cal_db, bridge_pkg):
    plugin_api = _load_plugin_api()
    cal = cal_db
    store = bridge_pkg.learning_store
    pol = bridge_pkg.policies

    pending = _propose_one_batch(
        cal_db, bridge_pkg, handle="style_idempotent", campaign_id="C_IDEM",
    )
    anchor_id = int(pending["identity_id"])
    body = plugin_api.ApprovalDecisionBody(
        identity_id=anchor_id,
        campaign_id=None,
        env="LIVE",
        decided_by="operator:test",
    )

    first = plugin_api.approve(
        store.STYLE_LEARNING_APPROVAL_FACT, body, x_bridge_key=None,
    )
    assert first.get("style_policy_apply")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        v1 = (pol.get_policy(conn, scope="company_style") or {}).get("version")

    second = plugin_api.approve(
        store.STYLE_LEARNING_APPROVAL_FACT, body, x_bridge_key=None,
    )
    assert second.get("idempotent_replay") is True

    with cal._connect() as conn:  # type: ignore[attr-defined]
        v2 = (pol.get_policy(conn, scope="company_style") or {}).get("version")
    assert v2 == v1
