from __future__ import annotations

from app.routers.products import _summarize_events, _summarize_outreach


def test_summarize_events_campaign_scope_does_not_drop_rows_without_product_sku() -> None:
    events = [
        {"id": 1, "campaign_id": "C1", "event_type": "shortlist_ready", "ts": "2026-06-01T10:00:00+00:00"},
        {"id": 2, "campaign_id": "C1", "event_type": "outbound_sent", "identity_id": 101, "ts": "2026-06-01T10:05:00+00:00"},
        {"id": 3, "campaign_id": "C1", "event_type": "kol_inbound_reply", "identity_id": 101, "ts": "2026-06-01T10:06:00+00:00"},
    ]

    summary = _summarize_events(events, campaign_id="C1", product_sku="SEB8008")

    assert summary["event_count"] == 3
    assert summary["last_event_type"] == "kol_inbound_reply"
    assert summary["sent_kol_ids"] == [101]
    assert summary["replied_kol_ids"] == [101]


def test_summarize_events_rollup_uses_campaign_membership_when_event_sku_missing() -> None:
    events = [
        {"id": 1, "campaign_id": "C-SKU", "event_type": "outbound_sent", "identity_id": 11, "ts": "2026-06-01T10:00:00+00:00"},
        {"id": 2, "campaign_id": "OTHER", "event_type": "outbound_sent", "identity_id": 22, "ts": "2026-06-01T10:01:00+00:00"},
    ]

    summary = _summarize_events(events, product_sku="SEB8008", campaign_skus={"C-SKU"})

    assert summary["event_count"] == 1
    assert summary["sent_kol_ids"] == [11]


def test_summarize_outreach_matches_kanban_bucket_semantics() -> None:
    items = [
        {"identity_id": 1, "candidate_status": "discovered", "reply_draft_state": "pending", "open_escalation_count": 0},
        {"identity_id": 2, "candidate_status": "selected_for_outreach", "interest_signal": "interested"},
        {"identity_id": 3, "candidate_status": "selected_for_outreach", "outreach_sent_at": "2026-06-01T10:00:00+00:00"},
        {"identity_id": 4, "candidate_status": "selected_for_outreach", "outreach_draft_created": True},
        {"identity_id": 5, "candidate_status": "discovered"},
    ]

    summary = _summarize_outreach(items)

    assert summary["outreach_counts"]["needs_attention"] == 1
    assert summary["outreach_counts"]["replied"] == 1
    assert summary["outreach_counts"]["sent"] == 1
    assert summary["outreach_counts"]["draft_ready"] == 1
    assert summary["outreach_counts"]["discovered"] == 1
