"""Read-path dedupe for reconcile-retried draft_edit_learning rows."""

from __future__ import annotations


def _write_edit_event(cal, *, iid: int, campaign_id: str, sent_message_id: str, dist: float):
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="draft_edit_learning",
        goal="outreach",
        lane="commerce",
        actor="gmail:sent-reconcile",
        payload={
            "was_edited": True,
            "edit_distance": dist,
            "sent_message_id": sent_message_id,
            "normalized_agent_body": "Agent body",
            "normalized_sent_body": "Sent body",
        },
        env="LIVE",
    )


def test_list_learning_events_dedupes_by_sent_message_id(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="dedupe-kol@test", env="LIVE")
    mid = "19eb-dedupe-test-msg"
    for _ in range(6):
        _write_edit_event(cal, iid=iid, campaign_id="C-DED1", sent_message_id=mid, dist=0.21)

    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = store.list_learning_events(
            conn,
            env="LIVE",
            event_types=("draft_edit_learning",),
            identity_id=iid,
            campaign_id="C-DED1",
            limit=30,
        )
    assert len(rows) == 1
    assert rows[0]["payload"]["sent_message_id"] == mid


def test_list_learning_events_keeps_distinct_sends(cal_db, bridge_pkg):
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="dedupe-two@test", env="LIVE")
    _write_edit_event(cal, iid=iid, campaign_id="C-DED2", sent_message_id="msg-a", dist=0.1)
    _write_edit_event(cal, iid=iid, campaign_id="C-DED2", sent_message_id="msg-a", dist=0.1)
    _write_edit_event(cal, iid=iid, campaign_id="C-DED2", sent_message_id="msg-b", dist=0.9)

    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = store.list_learning_events(
            conn,
            env="LIVE",
            event_types=("draft_edit_learning",),
            identity_id=iid,
            limit=30,
        )
    assert len(rows) == 2
    mids = {r["payload"]["sent_message_id"] for r in rows}
    assert mids == {"msg-a", "msg-b"}


def test_list_learning_events_excludes_bounce_and_failed_pair(cal_db, bridge_pkg):
    """Bounce DSN rows and their identity+campaign must not appear in learning reads."""
    store = bridge_pkg.learning_store
    cal = cal_db
    iid = cal.upsert_identity(primary_handle="bounce-exclude@test", env="LIVE")
    campaign_id = "C-BNC-EX1"
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="draft_edit_learning",
        goal="outreach",
        lane="commerce",
        actor="gmail:sent-reconcile",
        payload={
            "was_edited": True,
            "edit_distance": 0.9876,
            "sent_message_id": "msg-bounce-dsn",
            "normalized_agent_body": "Agent outreach body",
            "normalized_sent_body": (
                "** Address not found **\n\n"
                "Your message wasn't delivered to kol@x.com"
            ),
        },
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="draft_edit_learning",
        goal="outreach",
        lane="commerce",
        actor="gmail:sent-reconcile",
        payload={
            "was_edited": True,
            "edit_distance": 0.21,
            "sent_message_id": "msg-real-sent",
            "normalized_agent_body": "Agent outreach body",
            "normalized_sent_body": "Hi KOL, operator final body",
        },
        env="LIVE",
    )

    with cal._connect() as conn:  # type: ignore[attr-defined]
        rows = store.list_learning_events(
            conn,
            env="LIVE",
            event_types=("draft_edit_learning",),
            identity_id=iid,
            campaign_id=campaign_id,
            limit=10,
        )
    assert rows == []
