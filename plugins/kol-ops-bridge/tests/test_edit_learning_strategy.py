"""Style + strategy split from edit-learning distill."""

from __future__ import annotations


def test_slice_policy_md_no_goal_match_returns_empty(bridge_pkg):
    store = bridge_pkg.learning_store
    md = "## compensation_negotiation\n- Ask scope first\n## outreach\n- Skip\n"
    sliced = store.slice_policy_md_for_goals(
        md, ["interest_qualification"],
    )
    assert sliced == ""
    assert "compensation_negotiation" not in sliced


def test_slice_policy_md_returns_matching_goal_only(bridge_pkg):
    store = bridge_pkg.learning_store
    md = "## compensation_negotiation\n- Ask scope first\n## outreach\n- Skip\n"
    sliced = store.slice_policy_md_for_goals(
        md, ["compensation_negotiation"],
    )
    assert "Ask scope first" in sliced
    assert "outreach" not in sliced


def test_split_style_and_strategy_sections(bridge_pkg):
    distill = bridge_pkg.learning_distill
    md = (
        "## Proposed style updates\n\n"
        "- Be shorter\n\n"
        "## Proposed strategy updates\n\n"
        "## compensation_negotiation\n\n"
        "- Ask deliverables before price\n"
    )
    style, strategy = distill.split_style_and_strategy_markdown(md)
    assert "shorter" in style
    assert "deliverables" in strategy


def test_apply_merges_reply_strategy_policy(cal_db, bridge_pkg):
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store

    proposal = {
        "scope": "company_style",
        "proposed_style_markdown": "## Proposed style updates\n\n- tone fix\n",
        "proposed_strategy_markdown": (
            "## Proposed strategy updates\n\n"
            "## compensation_negotiation\n\n- clarify scope first\n"
        ),
    }
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = distill.apply_approved_style_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="test",
        )
    assert out.get("strategy_policy", {}).get("scope") == store.REPLY_STRATEGY_SCOPE
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        row = pol.get_policy(conn, scope=store.REPLY_STRATEGY_SCOPE, env="LIVE")
    assert row is not None
    assert "Approved strategy learning" in (row.get("content_md") or "")
    assert "compensation_negotiation" in (row.get("content_md") or "")


def test_build_learning_hints_skips_empty_goal_slice(cal_db, bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    pol = bridge_pkg.policies
    monkeypatch.setenv("KOL_STYLE_IN_HINTS", "0")
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="reply_learning",
            content_md="## compensation_negotiation\n- secret tactic\n",
            updated_by="test",
            env="LIVE",
        )
        hints = store.build_learning_hints(
            conn, env="LIVE", active_goals=["interest_qualification"],
        )
    policy_hints = [
        h for h in hints["hints"]
        if h.get("source") == "policy" and h.get("scope") == "reply_learning"
    ]
    assert policy_hints == []


def test_build_learning_hints_skips_policy_when_no_active_goals(cal_db, bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    pol = bridge_pkg.policies
    monkeypatch.setenv("KOL_STYLE_IN_HINTS", "0")
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="reply_strategy",
            content_md="## compensation_negotiation\n- tactic\n",
            updated_by="test",
            env="LIVE",
        )
        hints = store.build_learning_hints(conn, env="LIVE", active_goals=[])
    assert not [
        h for h in hints["hints"]
        if h.get("scope") in ("reply_learning", "reply_strategy")
    ]


def test_build_learning_hints_includes_company_style(cal_db, bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    pol = bridge_pkg.policies
    monkeypatch.setenv("KOL_STYLE_IN_HINTS", "1")
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="### kol-compensation-negotiator\n- Keep it warm and short.\n",
            updated_by="test",
        )
        hints = store.build_learning_hints(
            conn, env="LIVE", active_goals=["compensation_negotiation"],
        )
    style = [
        h for h in hints["hints"]
        if h.get("source") == "policy" and h.get("scope") == "company_style"
    ]
    assert style and "warm and short" in style[0]["content"]


def test_build_learning_hints_style_toggle_off(cal_db, bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    pol = bridge_pkg.policies
    monkeypatch.setenv("KOL_STYLE_IN_HINTS", "0")
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="### kol-compensation-negotiator\n- Keep it warm.\n",
            updated_by="test",
        )
        hints = store.build_learning_hints(
            conn, env="LIVE", active_goals=["compensation_negotiation"],
        )
    assert not [h for h in hints["hints"] if h.get("scope") == "company_style"]
