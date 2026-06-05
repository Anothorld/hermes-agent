"""Edit batch progress: pending proposals reserve events from the next batch count."""

from __future__ import annotations


def _insert_edit(cal, *, identity_id, campaign_id, env="LIVE"):
    cal.write_event(
        identity_id=identity_id,
        campaign_id=campaign_id,
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


def test_overview_available_drops_after_pending_proposal(cal_db, bridge_pkg, monkeypatch):
    overview_mod = bridge_pkg.learning_overview
    distill = bridge_pkg.learning_distill
    cal = cal_db
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "10")

    iid = cal.upsert_identity(primary_handle="progress_kol", env="LIVE")
    for i in range(12):
        _insert_edit(cal, identity_id=iid, campaign_id=f"C{i}")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        before = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=3)
        assert before["edit_stats"]["edited_unconsumed"] == 12
        assert before["edit_stats"]["edited_available"] == 12
        assert before["edit_stats"]["ready_for_distill"] is True

        distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            batch_size=10,
        )
        after = overview_mod.build_learning_overview(conn, env="LIVE", runs_limit=3)

    assert after["edit_stats"]["edited_unconsumed"] == 12
    assert after["edit_stats"]["edited_available"] == 2
    assert after["edit_stats"]["edited_queued_in_pending"] == 10
    assert after["edit_stats"]["ready_for_distill"] is False


def test_merge_outcome_uses_outcome_marker(bridge_pkg):
    distill = bridge_pkg.learning_distill
    current = "## Approved outcome learning\n\n- old rule\n"
    proposed = "## Approved outcome learning\n\n- new rule\n"
    merged = distill.merge_outcome_policy_content(
        current, proposed, mode="replace_section",
    )
    assert merged.count("## Approved outcome learning") == 1
    assert "new rule" in merged
    assert "old rule" not in merged
