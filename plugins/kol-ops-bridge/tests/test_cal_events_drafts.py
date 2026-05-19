"""Event log + draft history + reply tests."""

from __future__ import annotations


def test_record_event_appears_in_timeline(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    eid = cal_db.record_event(
        kol_identity_id=kid,
        event_type="emailed_initial",
        actor="chat",
        stage="outreach",
        sub_status="initial_drafted",
        payload={"draft_id": "r-1"},
    )
    assert eid is not None

    tl = cal_db.list_timeline(kid)
    assert len(tl["events"]) == 1
    assert tl["events"][0]["event_type"] == "emailed_initial"
    # payload is stored as JSON string in the timeline read.
    assert "r-1" in tl["events"][0]["payload_json"]


def test_record_draft_hashes_body_and_dedups_by_draft_id(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    cal_db.record_draft(
        kol_identity_id=kid,
        stage="initial",
        sub_status="initial_drafted",
        draft_id="r-1",
        subject="Hi",
        body="hello world",
        context_snapshot={"selling_point_group": "A"},
        actor="chat",
        triggered_by="chat",
    )
    first = cal_db.get_draft("r-1")
    assert first["body_hash"]  # sha256 hex
    assert len(first["body_hash"]) == 64

    # Re-writing the same draft_id must REPLACE, not duplicate.
    cal_db.record_draft(
        kol_identity_id=kid,
        stage="initial",
        sub_status="initial_drafted",
        draft_id="r-1",
        subject="Hi",
        body="hello world v2",
        context_snapshot={"selling_point_group": "A"},
        actor="chat",
        triggered_by="chat",
    )
    second = cal_db.get_draft("r-1")
    assert second["body"] == "hello world v2"
    assert second["body_hash"] != first["body_hash"]

    pending = cal_db.list_drafts_pending_review()
    assert len([d for d in pending if d["draft_id"] == "r-1"]) == 1


def test_mark_draft_sent_clears_pending_review(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    cal_db.record_draft(
        kol_identity_id=kid,
        stage="initial",
        draft_id="r-1",
        context_snapshot={},
        actor="chat",
        triggered_by="chat",
        body="x",
    )
    assert any(d["draft_id"] == "r-1" for d in cal_db.list_drafts_pending_review())
    cal_db.mark_draft_sent(draft_id="r-1")
    assert not any(d["draft_id"] == "r-1" for d in cal_db.list_drafts_pending_review())


def test_record_reply_appends_per_message(cal_db):
    kid = cal_db.upsert_identity(handle="k")
    cal_db.record_reply(
        kol_identity_id=kid,
        gmail_message_id="m-1",
        received_at="2026-05-19T10:00:00+00:00",
        match_strategy="threadId",
        match_confidence=1.0,
        intent="interested",
        confidence=0.9,
        handled_action="routed_skill",
    )
    cal_db.record_reply(
        kol_identity_id=kid,
        gmail_message_id="m-2",
        received_at="2026-05-19T11:00:00+00:00",
        match_strategy="email",
        match_confidence=0.8,
        intent="proposes_rate",
        confidence=0.85,
        handled_action="routed_skill",
    )
    tl = cal_db.list_timeline(kid)
    ids = {r["gmail_message_id"] for r in tl["replies"]}
    assert ids == {"m-1", "m-2"}
