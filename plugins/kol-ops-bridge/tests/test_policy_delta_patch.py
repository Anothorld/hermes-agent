"""Policy delta patch merge (replace_section default)."""

from __future__ import annotations


def test_replace_section_appends_new_rule_preserves_old(bridge_pkg):
    d = bridge_pkg.learning_distill
    base = "## Preamble\n- keep me\n"
    out1 = d.merge_style_policy_content(base, "- old rule", mode="replace_section")
    out2 = d.merge_style_policy_content(out1, "- new rule", mode="replace_section")
    assert "keep me" in out2
    assert "old rule" in out2
    assert "new rule" in out2


def test_patch_remove_drops_matching_bullet(bridge_pkg):
    d = bridge_pkg.learning_distill
    existing = (
        "## Approved strategy learning\n\n"
        "### goal\n\n"
        "- Keep warm tone in opener\n"
        "- Ask scope before price\n"
    )
    delta = "- REMOVE: Ask scope before price\n"
    patched = d.apply_policy_delta_patch(
        existing.split("## Approved strategy learning", 1)[1].strip(),
        delta,
    )
    assert "Keep warm tone" in patched
    assert "Ask scope before price" not in patched


def test_patch_adjust_replaces_matching_bullet(bridge_pkg):
    d = bridge_pkg.learning_distill
    existing = "- We want to introduce you to the product\n"
    delta = "- ADJUST: We want to introduce → We'd love to introduce you to the product\n"
    patched = d.apply_policy_delta_patch(existing, delta)
    assert "We'd love to introduce" in patched
    assert "We want to introduce" not in patched


def test_apply_replace_section_preserves_stale_when_adding(cal_db, bridge_pkg, monkeypatch):
    d = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    monkeypatch.setenv("KOL_STYLE_LEARNING_MERGE_MODE", "replace_section")
    proposal = {
        "scope": "company_style",
        "proposed_style_markdown": "## Proposed style updates\n- fresh rule",
        "proposed_strategy_markdown": "",
        "proposed_markdown": "## Proposed style updates\n- fresh rule",
    }
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="## Base\n- preamble rule\n\n## Approved style learning\n\n- stale rule\n",
            updated_by="test",
        )
        d.apply_approved_style_proposal(
            conn, env="LIVE", proposal=proposal, updated_by="test",
        )
        row = pol.get_policy(conn, scope="company_style")
    content = row["content_md"]
    assert "preamble rule" in content
    assert "fresh rule" in content
    assert "stale rule" in content
