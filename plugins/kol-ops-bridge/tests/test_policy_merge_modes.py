"""Stage C: policy merge modes (append / replace_section / llm_compress)."""

from __future__ import annotations


def test_append_mode_accumulates(bridge_pkg):
    d = bridge_pkg.learning_distill
    base = "## Preamble\n- keep me\n"
    out1 = d.merge_style_policy_content(base, "- rule A", mode="append")
    assert "## Approved style learning" in out1
    assert "rule A" in out1
    out2 = d.merge_style_policy_content(out1, "- rule B", mode="append")
    assert "rule A" in out2 and "rule B" in out2  # both accumulate


def test_replace_section_keeps_preamble_drops_old(bridge_pkg):
    d = bridge_pkg.learning_distill
    base = "## Preamble\n- keep me\n"
    out1 = d.merge_style_policy_content(base, "- old rule", mode="replace_section")
    assert "keep me" in out1 and "old rule" in out1
    out2 = d.merge_style_policy_content(out1, "- new rule", mode="replace_section")
    assert "keep me" in out2  # preamble preserved
    assert "new rule" in out2
    assert "old rule" not in out2  # old approved block replaced


def test_strategy_merge_marker(bridge_pkg):
    d = bridge_pkg.learning_distill
    out = d.merge_strategy_policy_content("", "## compensation\n- x", mode="append")
    assert "## Approved strategy learning" in out


def test_consolidate_policy_llm_uses_llm(bridge_pkg, monkeypatch):
    d = bridge_pkg.learning_distill
    monkeypatch.setattr(
        d.learning_llm,
        "invoke_learning_llm",
        lambda prompt, runner=None: "## Merged\n- consolidated rule\n",
    )
    out = d.consolidate_policy_llm(
        "## Old\n- a", "- ADJUST: a → b", scope="company_style",
    )
    assert "consolidated rule" in out


def test_apply_approved_respects_merge_mode(cal_db, bridge_pkg, monkeypatch):
    d = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    store = bridge_pkg.learning_store
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_MERGE_MODE", "replace_section")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="## Base\n- preamble rule\n\n## Approved style learning\n\n- stale rule\n",
            updated_by="test",
        )
        proposal = {
            "scope": "company_style",
            "proposed_style_markdown": "## Proposed style updates\n- fresh rule",
            "proposed_strategy_markdown": "",
            "proposed_markdown": "## Proposed style updates\n- fresh rule",
        }
        out = d.apply_approved_style_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="test",
        )
        row = pol.get_policy(conn, scope="company_style")
    assert out["merge_mode"] == "replace_section"
    content = row["content_md"]
    assert "preamble rule" in content
    assert "fresh rule" in content
    assert "stale rule" not in content
