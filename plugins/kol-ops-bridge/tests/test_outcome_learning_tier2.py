"""Step 5 Tier2: stratified outcome proposal + approval merge into outcome_strategy."""

from __future__ import annotations

import json


def _write_retro(cal, *, iid, campaign_id, outcome_class, tags, guidance, env="LIVE"):
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="collab_outcome_learning",
        goal=None,
        lane="meta",
        actor="test",
        payload={
            "outcome_class": outcome_class,
            "root_cause_tags": tags,
            "forward_guidance": guidance,
            "what_worked": [],
            "what_failed": [],
        },
        env=env,
    )


def test_threshold_min_failures_triggers_early(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_BATCH_SIZE", "10")
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_MIN_FAILURES", "3")
    monkeypatch.setattr(
        o.learning_llm, "invoke_learning_llm",
        lambda *a, **k: "## Approved outcome learning\n### compensation_negotiation\n- anchor lower",
    )
    iid = cal.upsert_identity(primary_handle="o_kol", env="LIVE")
    # Only 3 failures (< batch size 10) but meets min_failures.
    for i in range(3):
        _write_retro(cal, iid=iid, campaign_id=f"C{i}", outcome_class="failure",
                     tags=["price_too_high"], guidance=["anchor lower earlier"])
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.propose_outcome_learning_approval(conn, env="LIVE", updated_by="t")
    assert out.get("pending") is True
    assert out.get("failure_count") == 3


def test_below_threshold_skips(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_BATCH_SIZE", "5")
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_MIN_FAILURES", "3")
    iid = cal.upsert_identity(primary_handle="o_kol2", env="LIVE")
    _write_retro(cal, iid=iid, campaign_id="C1", outcome_class="success",
                 tags=["great_fit"], guidance=[])
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.propose_outcome_learning_approval(conn, env="LIVE", updated_by="t")
    assert out.get("skipped") is True
    assert out.get("reason") == "below_outcome_threshold"


def test_apply_approved_outcome_merges_policy(cal_db, bridge_pkg):
    o = bridge_pkg.learning_outcome
    pol = bridge_pkg.policies
    cal = cal_db
    with cal._connect() as conn:  # type: ignore[attr-defined]
        proposal = {
            "scope": "outcome_strategy",
            "proposed_markdown": "## Approved outcome learning\n### compensation_negotiation\n- anchor lower earlier",
        }
        out = o.apply_approved_outcome_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="op",
        )
        row = pol.get_policy(conn, scope="outcome_strategy", env="LIVE")
    assert out["scope"] == "outcome_strategy"
    assert "anchor lower earlier" in row["content_md"]


def test_consumed_events_not_reproposed(cal_db, bridge_pkg, monkeypatch):
    o = bridge_pkg.learning_outcome
    cal = cal_db
    monkeypatch.setenv("KOL_OUTCOME_LEARNING_MIN_FAILURES", "1")
    monkeypatch.setattr(
        o.learning_llm, "invoke_learning_llm", lambda *a, **k: "## Approved outcome learning\n- x",
    )
    iid = cal.upsert_identity(primary_handle="o_kol3", env="LIVE")
    _write_retro(cal, iid=iid, campaign_id="C1", outcome_class="failure",
                 tags=["x"], guidance=["g"])
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = o.propose_outcome_learning_approval(conn, env="LIVE", updated_by="t")
        eids = out["source_event_ids"]
        # Mark approved by writing the fact with decision=approved.
        val = {
            "decision": "approved",
            "scope": "outcome_strategy",
            "source_event_ids": eids,
        }
        cal.write_facts(
            identity_id=iid, campaign_id=None, namespace="approval",
            facts={o.OUTCOME_LEARNING_APPROVAL_FACT: val}, source="test", env="LIVE",
        )
        consumed = o.list_consumed_outcome_event_ids(conn, env="LIVE")
    assert set(eids).issubset(consumed)
