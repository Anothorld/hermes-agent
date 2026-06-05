"""Stage B: newest-first sampling, KOL diversity, and baseline-aware distill prompt."""

from __future__ import annotations

import json


def _ev(eid, iid, cid="C1"):
    return {
        "id": eid,
        "identity_id": iid,
        "campaign_id": cid,
        "payload": {"was_edited": True, "edit_distance": 0.3, "child_skill": "s"},
    }


def test_select_batch_newest_first(bridge_pkg):
    distill = bridge_pkg.learning_distill
    edited = [_ev(i, identity := 100 + i) for i in range(1, 11)]  # ids 1..10
    batch = distill._select_edit_batch(
        edited, threshold=3, order="newest", min_distinct=0,
    )
    ids = [e["id"] for e in batch]
    # Newest 3 = ids 8,9,10, presented chronologically.
    assert ids == [8, 9, 10]


def test_select_batch_oldest(bridge_pkg):
    distill = bridge_pkg.learning_distill
    edited = [_ev(i, 100 + i) for i in range(1, 11)]
    batch = distill._select_edit_batch(
        edited, threshold=3, order="oldest", min_distinct=0,
    )
    assert [e["id"] for e in batch] == [1, 2, 3]


def test_select_batch_min_distinct_identities(bridge_pkg):
    distill = bridge_pkg.learning_distill
    # 8 newest events all from identity 200; older ones from 201,202,203.
    edited = [_ev(i, 200) for i in range(5, 13)] + [
        _ev(4, 203), _ev(3, 202), _ev(2, 201), _ev(1, 200),
    ]
    batch = distill._select_edit_batch(
        edited, threshold=4, order="newest", min_distinct=3,
    )
    identities = {e["identity_id"] for e in batch}
    # Must cover >= 3 distinct identities despite newest being one KOL.
    assert len(identities) >= 3


def test_baseline_injected_into_prompt(cal_db, bridge_pkg, monkeypatch):
    distill = bridge_pkg.learning_distill
    pol = bridge_pkg.policies
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="baseline_kol", env="LIVE")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        pol.put_policy(
            conn,
            scope="company_style",
            content_md="## Baseline\n- Always sign as POVISON Team.\n",
            updated_by="test",
        )
        captured = {}

        def fake_runner(prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                "## Proposed style updates\n### s\n- ADJUST: warmer opener.\n\n"
                "## Proposed strategy updates\n### outreach\n- Ask earlier.\n"
            )

        monkeypatch.setattr(
            distill.learning_llm,
            "invoke_learning_llm",
            lambda prompt, runner=None: fake_runner(prompt),
        )
        events = [
            {
                "id": 1,
                "identity_id": iid,
                "campaign_id": "C1",
                "goal": "outreach",
                "payload": {
                    "was_edited": True,
                    "edit_distance": 0.4,
                    "child_skill": "s",
                    "normalized_agent_body": "A",
                    "normalized_sent_body": "B",
                },
            },
        ]
        style_md, strategy_md, used = distill.distill_edit_learning_llm(
            conn, events, style_scope="company_style", env="LIVE",
        )
    assert used is True
    assert "CURRENT APPROVED GUIDELINES" in captured["prompt"]
    assert "Always sign as POVISON Team" in captured["prompt"]
    assert "Proposed style updates" in style_md
    assert "Proposed strategy updates" in strategy_md
