"""Phase F state-machine regression tests.

Locks down behaviours the plan in plan.md (Phase F1–F5) requires but
that the codebase did not previously enforce in tests:

1. Opening a child escalation with ``parent_escalation_id`` must
   transition the parent out of any non-terminal state into
   ``re_escalated`` (was previously leaving the parent stuck in
   ``answered`` — observed in escalation #3 of TS8136 real-data run).
2. ``cal.resolve_escalation`` must reject unknown ``final_state``
   values (e.g. ``declined`` / ``abandoned`` were previously written
   to the DB unchecked).
3. ``attempts_count`` chains correctly across parent → child and
   ``force_human_takeover_hint`` is stamped on ``resume_context`` once
   ``max_escalation_depth`` is reached.
4. ``resolved`` → goal reverts to ``active``;
   ``aborted`` → goal flips to ``aborted``;
   ``re_escalated`` (auto-set by child open) leaves goal blocked.
"""

from __future__ import annotations

import pytest


CAMPAIGN = "C-flow"


def _bootstrap(cal, cid: str = CAMPAIGN) -> int:
    cal.upsert_campaign_config(
        campaign_id=cid, label="flow", env="TEST",
        sku_whitelist=["SKU-A"],
        deliverable_platforms=["instagram"],
        deliverable_count_per_platform=1,
        paid_ceiling=1000.0,
        contract_required=True,
    )
    iid = cal.upsert_identity(primary_handle="kola")
    cal.write_facts(
        identity_id=iid, campaign_id=cid, namespace="offer",
        facts={"offer.outreach_sent": True,
               "offer.interest_signal": "confirmed"},
        source="seed", env="TEST",
    )
    return iid


def test_child_escalation_transitions_parent_to_re_escalated(cal_db):
    """Phase F1/F5: parent must move out of awaiting_answer once child opens.

    Was the root cause of escalation #3 in the user's TS8136 run sitting
    in ``state=answered`` forever after the resumer opened #4.
    """
    cal = cal_db
    iid = _bootstrap(cal)
    parent = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection",
        reason="missing_test_mode_to_in_cal",
        question_to_operator="provide test inbox?",
    )
    assert parent is not None

    # Operator partially answered → resumer decides to escalate again.
    cal.resolve_escalation(
        escalation_id=parent, decision="resume", decided_by="op:zoe",
        operator_answer="see follow-up", final_state="answered",
    )

    child = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection",
        reason="missing_public_email_for_initial_outreach",
        parent_escalation_id=parent,
    )
    assert child is not None

    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    assert rows[parent]["state"] == "re_escalated"
    assert rows[child]["state"] == "awaiting_answer"
    assert rows[child]["attempts_count"] == 2


def test_child_open_also_promotes_resolved_parent(cal_db):
    """Even if parent was hastily marked ``resolved`` (e.g. console
    defaulted final_state=resolved), a follow-up child must re-flag the
    parent as ``re_escalated`` so the audit trail is honest.
    """
    cal = cal_db
    iid = _bootstrap(cal)
    parent = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="ambiguous_request",
    )
    cal.resolve_escalation(
        escalation_id=parent, decision="resume",
        decided_by="op:zoe", final_state="resolved",
    )
    cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="still_ambiguous",
        parent_escalation_id=parent,
    )
    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    assert rows[parent]["state"] == "re_escalated"


def test_resolve_escalation_rejects_unknown_final_state(cal_db):
    """Phase F1: only the documented state enum may be written."""
    cal = cal_db
    iid = _bootstrap(cal)
    eid = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="x",
    )
    with pytest.raises(cal.EscalationStateError):
        cal.resolve_escalation(
            escalation_id=eid, decision="resume",
            decided_by="op:zoe", final_state="declined",
        )
    with pytest.raises(cal.EscalationStateError):
        cal.resolve_escalation(
            escalation_id=eid, decision="resume",
            decided_by="op:zoe", final_state="abandoned",
        )


def test_attempts_count_chains_and_emits_takeover_hint(cal_db):
    """Phase F4: when attempts_count reaches max_escalation_depth, the
    bridge stamps ``force_human_takeover_hint=true`` in resume_context.
    Default max depth = 3 (the bridge falls back to that when the
    escalation_rules policy is absent).
    """
    cal = cal_db
    iid = _bootstrap(cal)
    e1 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r1",
    )
    e2 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r2",
        parent_escalation_id=e1,
    )
    e3 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r3",
        parent_escalation_id=e2,
    )
    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    assert rows[e1]["attempts_count"] == 1
    assert rows[e2]["attempts_count"] == 2
    assert rows[e3]["attempts_count"] == 3
    ctx3 = rows[e3]["resume_context"]
    assert ctx3.get("force_human_takeover_hint") is True
    assert ctx3.get("max_escalation_depth") == 3
    assert ctx3.get("attempts_count") == 3


def test_aborted_marks_goal_aborted(cal_db):
    cal = cal_db
    iid = _bootstrap(cal)
    eid = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="hard_no",
    )
    cal.resolve_escalation(
        escalation_id=eid, decision="reject",
        decided_by="op:zoe", final_state="aborted",
    )
    g = {x["goal"]: x for x in cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
    )}
    assert g["product_selection"]["status"] == "aborted"


def test_resolved_returns_goal_to_active_and_clears_blocking_id(cal_db):
    cal = cal_db
    iid = _bootstrap(cal)
    eid = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="ask_for_alt_sku",
    )
    g = {x["goal"]: x for x in cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
    )}
    assert g["product_selection"]["status"] == "blocked"
    assert g["product_selection"]["blocking_escalation_id"] == eid

    cal.resolve_escalation(
        escalation_id=eid, decision="resume",
        decided_by="op:zoe", final_state="resolved",
    )
    g = {x["goal"]: x for x in cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
    )}
    assert g["product_selection"]["status"] == "active"
    assert g["product_selection"]["blocking_escalation_id"] is None


def test_max_depth_never_auto_aborts(cal_db):
    """Phase F4: at ``attempts_count >= max_escalation_depth`` the
    bridge stamps the takeover hint but MUST NOT auto-abort. Only the
    operator (via the console terminate button) may move a goal to
    ``aborted``. Regression guard for any future automation that
    might try to "shortcut" deep chains.
    """
    cal = cal_db
    iid = _bootstrap(cal)
    e1 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r1",
    )
    e2 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r2",
        parent_escalation_id=e1,
    )
    e3 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r3",
        parent_escalation_id=e2,
    )
    # The top of the chain (#3) carries the hint.
    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    assert rows[e3]["state"] == "awaiting_answer"
    assert rows[e3]["resume_context"].get("force_human_takeover_hint") is True
    # Goal remains blocked — not aborted, not satisfied.
    g = {x["goal"]: x for x in cal.get_goal_state(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
    )}
    assert g["product_selection"]["status"] == "blocked"
    assert g["product_selection"]["blocking_escalation_id"] == e3


def test_deeper_chain_still_increments_attempts(cal_db):
    """Past the default depth (3), attempts_count keeps growing — the
    bridge does not clamp it. This matters because the operator may
    want a long audit trail of escalations even after the hint fires.
    """
    cal = cal_db
    iid = _bootstrap(cal)
    parent = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r0",
    )
    for i in range(1, 6):  # build a 6-deep chain
        parent = cal.open_escalation(
            identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
            goal="product_selection", reason=f"r{i}",
            parent_escalation_id=parent,
        )
    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    assert rows[parent]["attempts_count"] == 6
    assert rows[parent]["resume_context"].get("force_human_takeover_hint") is True


def test_policy_override_max_depth(cal_db):
    """Phase E1 + F4: ``escalation_rules`` policy may override
    ``max_escalation_depth`` via a top-level ``max_escalation_depth:
    <n>`` line. The bridge honours it on the next ``open_escalation``.
    """
    import importlib.util, sys
    from pathlib import Path
    plugin_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_pkg.policies", plugin_root / "policies.py",
    )
    assert spec and spec.loader
    policies = importlib.util.module_from_spec(spec)
    sys.modules["kol_ops_bridge_pkg.policies"] = policies
    spec.loader.exec_module(policies)

    cal = cal_db
    iid = _bootstrap(cal)
    with cal._connect() as conn:
        policies.put_policy(
            conn, scope="escalation_rules",
            content_md="max_escalation_depth: 2\n\n# rules\n",
            updated_by="owner@console.app",
        )
    e1 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r1",
    )
    e2 = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, env="TEST",
        goal="product_selection", reason="r2",
        parent_escalation_id=e1,
    )
    rows = {r["id"]: r for r in cal.list_escalations(env="TEST")}
    # max_escalation_depth=2 → the *second* escalation already trips
    # the hint, not waiting for the third.
    assert rows[e2]["resume_context"].get("force_human_takeover_hint") is True
    assert rows[e2]["resume_context"].get("max_escalation_depth") == 2
