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


def test_strip_proposal_context_notes_zh_background(bridge_pkg):
    distill = bridge_pkg.learning_distill
    md = (
        "## Approved discovery learning\n"
        "### Preferred KOL profile\n"
        "- keep this\n"
        "### 背景说明\n"
        "- 批次大小：31\n"
    )
    body = distill.proposal_section_for_policy_merge(md)
    assert "keep this" in body
    assert "背景说明" not in body
    assert "批次大小" not in body


def test_strip_proposal_context_notes(bridge_pkg):
    distill = bridge_pkg.learning_distill
    md = (
        "## Proposed strategy updates\n\n"
        "## compensation_negotiation\n\n"
        "- Ask scope first\n\n"
        "### Context notes\n\n"
        "- Batch size: 5\n"
    )
    body = distill.proposal_section_for_policy_merge(md)
    assert "Ask scope first" in body
    assert "Context notes" not in body
    assert "Batch size" not in body


def test_is_actionable_policy_delta_skips_no_new_rules(bridge_pkg):
    distill = bridge_pkg.learning_distill
    md = (
        "## Proposed strategy updates\n\n"
        "### interest_qualification\n\n"
        "- **No new strategy rules emerge from this batch.**\n\n"
        "### Context notes\n\n"
        "- Batch size: 5\n"
    )
    assert not distill.is_actionable_policy_delta(md)


def test_apply_skips_non_actionable_strategy_preserves_policy(cal_db, bridge_pkg):
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store

    proposal = {
        "scope": "company_style",
        "proposed_style_markdown": (
            "## Proposed style updates\n\n"
            "- **No new style rules emerge from this batch.**\n"
        ),
        "proposed_strategy_markdown": (
            "## Proposed strategy updates\n\n"
            "- **No new strategy rules emerge from this batch.**\n\n"
            "### Context notes\n\n"
            "- Batch size: 5\n"
        ),
    }
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope=store.REPLY_STRATEGY_SCOPE,
            content_md="## Approved strategy learning\n\n- keep this rule\n",
            updated_by="test",
            env="LIVE",
        )
        out = distill.apply_approved_style_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="test",
        )
        row = pol.get_policy(conn, scope=store.REPLY_STRATEGY_SCOPE, env="LIVE")
    assert out.get("strategy_policy", {}).get("skipped") is True
    assert "keep this rule" in (row.get("content_md") or "")
    assert "Batch size" not in (row.get("content_md") or "")


def test_sanitize_removes_context_notes_from_stored_policy(cal_db, bridge_pkg):
    d = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store
    dirty = (
        "## Approved strategy learning\n\n"
        "## Proposed strategy updates\n\n"
        "### interest_qualification\n\n"
        "- Keep this rule\n\n"
        "### Context notes\n\n"
        "- Batch size: 5\n"
    )
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope=store.REPLY_STRATEGY_SCOPE,
            content_md=dirty,
            updated_by="test",
            env="LIVE",
        )
        out = d.sanitize_stored_policy_learning_metadata(
            conn,
            scope=store.REPLY_STRATEGY_SCOPE,
            env="LIVE",
            updated_by="test:sanitize",
        )
        row = pol.get_policy(conn, scope=store.REPLY_STRATEGY_SCOPE, env="LIVE")
    assert out.get("had_context_notes") is True
    assert out.get("removed_chars", 0) > 0
    content = row.get("content_md") or ""
    assert "Keep this rule" in content
    assert "Context notes" not in content
    assert "Batch size" not in content


def test_apply_merges_actionable_rule_without_context_notes(cal_db, bridge_pkg):
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store

    proposal = {
        "scope": "company_style",
        "proposed_style_markdown": "## Proposed style updates\n\n- tone fix\n",
        "proposed_strategy_markdown": (
            "## Proposed strategy updates\n\n"
            "## compensation_negotiation\n\n"
            "- clarify scope first\n\n"
            "### Context notes\n\n"
            "- Batch size: 5\n"
        ),
    }
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        out = distill.apply_approved_style_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="test",
        )
        row = pol.get_policy(conn, scope=store.REPLY_STRATEGY_SCOPE, env="LIVE")
    assert out.get("strategy_policy", {}).get("scope") == store.REPLY_STRATEGY_SCOPE
    content = row.get("content_md") or ""
    assert "clarify scope first" in content
    assert "Context notes" not in content
    assert "Batch size" not in content
