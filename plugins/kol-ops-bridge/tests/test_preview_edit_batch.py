"""Preview next distill batch + policy merge preview."""

from __future__ import annotations


def _insert_edit(cal, *, iid, cid, env="LIVE", ts=None):
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="draft_edit_learning",
        goal="outreach",
        lane="commerce",
        actor="test",
        payload={
            "was_edited": True,
            "child_skill": "kol-reply-synthesizer",
            "edit_distance": 0.3,
            "normalized_agent_body": "A",
            "normalized_sent_body": "B",
        },
        env=env,
    )


def test_preview_below_threshold_returns_numeric_edited_available(
    cal_db, bridge_pkg, monkeypatch,
):
    distill = bridge_pkg.learning_distill
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "5")
    iid = cal.upsert_identity(primary_handle="prev_one", env="LIVE")
    _insert_edit(cal, iid=iid, cid="C1")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = distill.preview_next_style_edit_batch(
            conn, env="LIVE", scope="company_style",
        )
    assert out.get("ready") is False
    assert out.get("reason") == "below_style_learning_batch_threshold"
    assert out.get("edited_available") == 1
    assert isinstance(out.get("edited_available"), int)


def test_preview_next_batch_lists_samples(cal_db, bridge_pkg, monkeypatch):
    distill = bridge_pkg.learning_distill
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "2")
    iid = cal.upsert_identity(primary_handle="prev_kol", env="LIVE")
    _insert_edit(cal, iid=iid, cid="C1")
    _insert_edit(cal, iid=iid, cid="C2")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = distill.preview_next_style_edit_batch(
            conn, env="LIVE", scope="company_style",
        )
    assert out.get("ready") is True
    assert len(out.get("samples") or []) == 2


def test_policy_merge_preview_style(cal_db, bridge_pkg):
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    cal = cal_db
    with cal._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="# Base\n",
            updated_by="t",
            env=None,
        )
        out = distill.preview_policy_merge_from_proposal(
            conn,
            env="LIVE",
            proposal={
                "scope": "company_style",
                "proposed_style_markdown": "## Approved style learning\n\n- new rule",
            },
        )
    assert "style" in out.get("sections", {})
    assert "new rule" in out["sections"]["style"]["merged_md"]
    assert out["sections"]["style"].get("merge_effect") == "add_new"


def test_learning_defaults(bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    distill = bridge_pkg.learning_distill
    monkeypatch.delenv("KOL_STYLE_LEARNING_BATCH_SIZE", raising=False)
    monkeypatch.delenv("KOL_STYLE_LEARNING_MERGE_MODE", raising=False)
    assert store.style_learning_batch_size() == 5
    assert distill._merge_mode() == "replace_section"
