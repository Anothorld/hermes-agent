"""Style learning distill must not fall back to rule aggregation."""

from __future__ import annotations

import pytest


def test_distill_raises_when_llm_fails(cal_db, bridge_pkg, monkeypatch):
    distill = bridge_pkg.learning_distill
    llm = bridge_pkg.learning_llm
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="llm_req_kol", env="LIVE")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, 'C1', 'draft_edit_learning', 'outreach', 'commerce', 't',
                       datetime('now'), ?, 'LIVE')""",
            (
                iid,
                '{"was_edited": true, "edit_distance": 0.2, "child_skill": "s", '
                '"normalized_agent_body": "A", "normalized_sent_body": "B"}',
            ),
        )
        conn.commit()
        events = bridge_pkg.learning_store.list_learning_events(
            conn, env="LIVE", event_types=("draft_edit_learning",), limit=5,
        )
        monkeypatch.setattr(
            llm,
            "invoke_learning_llm",
            lambda *_a, **_k: (_ for _ in ()).throw(
                llm.LearningLlmError("simulated outage"),
            ),
        )
        with pytest.raises(llm.LearningLlmError, match="simulated"):
            distill.distill_edit_learning_llm(
                conn, events, style_scope="company_style", env="LIVE",
            )
