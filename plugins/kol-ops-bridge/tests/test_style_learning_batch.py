"""Style learning batch threshold + rich LLM context."""

from __future__ import annotations

import json


def _insert_edit_event(
    cal_db,
    *,
    identity_id: int,
    campaign_id: str,
    env: str = "LIVE",
    event_id: int | None = None,
) -> int:
    cal = cal_db
    payload = {
        "was_edited": True,
        "child_skill": "kol-reply-synthesizer",
        "edit_distance": 0.12,
        "normalized_agent_body": "Hi there",
        "normalized_sent_body": "Hello!",
        "sent_message_id": "msg-in-1",
    }
    with cal._connect() as conn:  # type: ignore[attr-defined]
        cur = conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'draft_edit_learning', 'outreach', 'commerce', 'test', datetime('now'), ?, ?)""",
            (identity_id, campaign_id, json.dumps(payload), env),
        )
        conn.commit()
        return int(cur.lastrowid)


def _insert_inbound(cal_db, *, identity_id: int, campaign_id: str, env: str = "LIVE") -> None:
    with cal_db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO kol_conversation_events
               (identity_id, campaign_id, event_type, goal, lane, actor, ts, payload_json, env)
               VALUES (?, ?, 'kol_inbound_reply', 'outreach', 'commerce', 'test', datetime('now'), ?, ?)""",
            (
                identity_id,
                campaign_id,
                json.dumps({
                    "message_id": "msg-in-1",
                    "from_addr": "kol@example.com",
                    "subject": "Re: collab",
                    "body": "What deliverables do you need?",
                }),
                env,
            ),
        )
        conn.commit()


def test_batch_threshold_blocks_proposal(cal_db, bridge_pkg, monkeypatch):
    monkeypatch.setenv("KOL_STYLE_LEARNING_BATCH_SIZE", "10")
    cal = cal_db
    distill = bridge_pkg.learning_distill
    store = bridge_pkg.learning_store

    assert store.style_learning_batch_size() == 10

    iid = cal.upsert_identity(primary_handle="batch_kol", env="LIVE")
    _insert_edit_event(cal_db, identity_id=iid, campaign_id="C_BATCH")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            limit=50,
        )
    assert out.get("skipped") is True
    assert out.get("reason") == "below_style_learning_batch_threshold"
    assert out.get("pending_edits") == 1


def test_rich_sample_includes_facts_and_timeline(cal_db, bridge_pkg):
    cal = cal_db
    store = bridge_pkg.learning_store

    iid = cal.upsert_identity(primary_handle="rich_kol", env="LIVE")
    cal.write_facts(
        identity_id=iid,
        campaign_id="C_RICH",
        namespace="offer",
        facts={"offer.interest_signal": "needs_more_info"},
        source="test",
        env="LIVE",
    )
    _insert_inbound(cal_db, identity_id=iid, campaign_id="C_RICH")
    eid = _insert_edit_event(cal_db, identity_id=iid, campaign_id="C_RICH")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT * FROM kol_conversation_events WHERE id=?", (eid,),
        ).fetchone()
        ev = dict(row)
        ev["payload"] = json.loads(ev["payload_json"])
        sample = store.build_style_learning_sample(conn, ev, env="LIVE")

    assert sample["current_facts"].get("offer.interest_signal") == "needs_more_info"
    assert any(t.get("event_type") == "kol_inbound_reply" for t in sample["conversation_timeline"])
    assert sample["edit"]["normalized_agent_body"]


def test_propose_with_batch_size_one(cal_db, bridge_pkg):
    cal = cal_db
    distill = bridge_pkg.learning_distill

    iid = cal.upsert_identity(primary_handle="batch1_kol", env="LIVE")
    _insert_edit_event(cal_db, identity_id=iid, campaign_id="C_ONE")

    with cal._connect() as conn:  # type: ignore[attr-defined]
        out = distill.propose_style_learning_approval(
            conn,
            env="LIVE",
            scope="company_style",
            updated_by="test",
            limit=50,
            batch_size=1,
        )
    assert out.get("pending") is True
    assert out.get("batch_threshold") == 1
