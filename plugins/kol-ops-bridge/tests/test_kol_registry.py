"""Tests for discovered-KOL registry list (metrics table)."""

from __future__ import annotations

import datetime as _dt

import pytest


def _backdate_candidate(cal, *, campaign_id: str, identity_id: int, days: int) -> None:
    """Shift ``campaign_candidates.created_at`` into the past for funnel maturity."""
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    ).isoformat(timespec="seconds")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """UPDATE campaign_candidates
                  SET created_at = ?, updated_at = ?
                WHERE campaign_id = ? AND identity_id = ?""",
            (cutoff, cutoff, campaign_id, identity_id),
        )
        conn.commit()


def _backdate_events(cal, *, identity_id: int, days_ago: int) -> None:
    """Shift all events for an identity into the past (draft-within-window tests)."""
    ts = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_ago)
    ).isoformat(timespec="seconds")
    with cal._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "UPDATE kol_conversation_events SET ts = ? WHERE identity_id = ?",
            (ts, identity_id),
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _registry_touch_count_mock(monkeypatch, bridge_pkg):
    """Default: spreadsheet touch counts are mocked per test."""
    pta = bridge_pkg.prior_touch_allowlist
    monkeypatch.setattr(pta, "get_internal_touch_count", lambda **_: 0)
    monkeypatch.setattr(pta, "is_prior_touch_allowlisted", lambda **_: False)


def test_registry_lists_all_discovered_statuses(bridge_pkg, cal_db, monkeypatch):
    pta = bridge_pkg.prior_touch_allowlist

    def _touch(*, handle=None, email=None, **_):
        if str(handle or "").lstrip("@") == "shortlisted":
            return 1
        return 0

    monkeypatch.setattr(pta, "get_internal_touch_count", _touch)
    cal = cal_db
    cid = "REG-CAMP-1"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid_disc = cal.upsert_identity(
        primary_handle="@disc_only",
        primary_email="d@example.com",
        env="LIVE",
    )
    iid_short = cal.upsert_identity(
        primary_handle="@shortlisted",
        primary_email="s@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_disc, source="discovery", env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_short, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid,
        identity_ids=[iid_short],
        candidate_status="shortlisted",
        env="LIVE",
    )
    cal.write_facts(
        identity_id=iid_short,
        campaign_id=cid,
        namespace="identity",
        facts={"identity.nox_followers": 12000, "identity.nox_avg_views": 4500},
        source="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_short,
        campaign_id=cid,
        event_type="outbound_draft_created",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", limit=50, offset=0)
    assert out["total"] == 2
    by_id = {row["identity_id"]: row for row in out["items"]}
    assert iid_disc in by_id
    assert iid_short in by_id
    assert by_id[iid_disc]["internal_touch_count"] == 0
    assert by_id[iid_short]["internal_touch_count"] == 1
    assert by_id[iid_short]["followers"] == 12000
    assert by_id[iid_short]["avg_views"] == 4500


def test_registry_excludes_legacy_import_only(bridge_pkg, cal_db):
    cal = cal_db
    campaign_id = "legacy-redlist-20240624-barbara-teerlink-b48a271617"
    iid = cal.upsert_identity(
        primary_handle="@barbara.teerlink",
        primary_email="babiteerlink@gmail.com",
        env="LIVE",
    )
    cal.write_facts(
        identity_id=iid,
        campaign_id=campaign_id,
        namespace="identity",
        facts={
            "identity.legacy_import_id": "redlist:b48a271617",
            "identity.follower_count": "127",
            "identity.social_links": [
                "https://www.instagram.com/barbara.teerlink/",
            ],
        },
        source="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid,
        campaign_id=campaign_id,
        event_type="legacy.collab_imported",
        goal="post_collab_archival",
        lane="meta",
        actor="manual:legacy-md-import",
        payload={"import_id": "redlist:b48a271617", "handle": "barbara.teerlink"},
        env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", q="barbara.teerlink", limit=10)
    assert out["total"] == 0
    assert out["items"] == []


def test_registry_sort_by_ingested_at(bridge_pkg, cal_db):
    cal = cal_db
    cid = "SORT-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid_old = cal.upsert_identity(
        primary_handle="@sort_old",
        primary_email="old@example.com",
        env="LIVE",
    )
    iid_new = cal.upsert_identity(
        primary_handle="@sort_new",
        primary_email="new@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_old, source="discovery", env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_new, source="discovery", env="LIVE",
    )
    with cal._connect() as conn:  # noqa: SLF001 — test timing adjustment
        conn.execute(
            "UPDATE campaign_candidates SET created_at=? "
            "WHERE identity_id=? AND env='LIVE'",
            ("2026-05-01T00:00:00+00:00", iid_old),
        )
        conn.execute(
            "UPDATE campaign_candidates SET created_at=? "
            "WHERE identity_id=? AND env='LIVE'",
            ("2026-06-01T00:00:00+00:00", iid_new),
        )
        conn.commit()

    desc = cal.list_discovered_kol_registry(
        env="LIVE", q="sort_", sort="ingested_at", order="desc", limit=10,
    )
    handles_desc = [str(r["handle"]).lstrip("@") for r in desc["items"][:2]]
    assert handles_desc == ["sort_new", "sort_old"]

    asc = cal.list_discovered_kol_registry(
        env="LIVE", q="sort_", sort="ingested_at", order="asc", limit=10,
    )
    handles_asc = [str(r["handle"]).lstrip("@") for r in asc["items"][:2]]
    assert handles_asc == ["sort_old", "sort_new"]


def test_registry_search_by_handle(bridge_pkg, cal_db):
    cal = cal_db
    cid = "REG-CAMP-2"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@unique_handle_xyz",
        primary_email="u@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", q="unique_handle", limit=10)
    assert out["total"] == 1
    assert out["items"][0]["identity_id"] == iid


def test_registry_touch_count_from_workbook_index(bridge_pkg, cal_db, monkeypatch):
    cal = cal_db
    pta = bridge_pkg.prior_touch_allowlist
    monkeypatch.setattr(
        pta,
        "get_internal_touch_count",
        lambda *, handle=None, email=None, **_:
            3 if str(handle or "").lstrip("@") == "sheet_match_kol" else 0,
    )
    cid = "TOUCH-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@sheet_match_kol",
        primary_email="sheet@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", q="sheet_match_kol", limit=5)
    assert out["items"][0]["internal_touch_count"] == 3


def test_batch_internal_touch_count_matches_registry_logic(bridge_pkg, cal_db, monkeypatch):
    cal = cal_db
    pta = bridge_pkg.prior_touch_allowlist
    monkeypatch.setattr(
        pta,
        "get_internal_touch_count",
        lambda *, handle=None, email=None, **_:
            2 if str(handle or "").lstrip("@") == "batch_touch_kol" else 0,
    )
    iid = cal.upsert_identity(
        primary_handle="@batch_touch_kol",
        primary_email="batch@example.com",
        env="LIVE",
    )
    out = cal.batch_internal_touch_count([iid], env="LIVE", handles=["orphan_handle"])
    assert out[str(iid)] == 2
    assert out["h:orphan_handle"] == 0


def test_registry_pipeline_flags_initial_draft_and_reply(bridge_pkg, cal_db):
    cal = cal_db
    cid = "PIPE-CAMP-1"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@pipeline_kol",
        primary_email="pipe@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_initial_outreach_draft_ready",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="outbound_draft_created",
        goal="outreach",
        lane="commerce",
        actor="approval:test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        goal="",
        lane="",
        actor="cron",
        env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", q="pipeline_kol", limit=5)
    row = out["items"][0]
    assert row["has_initial_outreach_draft"] is True
    assert row["has_inbound_reply"] is True


def test_registry_pipeline_flags_default_false(bridge_pkg, cal_db):
    cal = cal_db
    cid = "PIPE-CAMP-2"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@pipeline_empty",
        primary_email="empty@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )

    out = cal.list_discovered_kol_registry(env="LIVE", q="pipeline_empty", limit=5)
    row = out["items"][0]
    assert row["has_initial_outreach_draft"] is False
    assert row["has_inbound_reply"] is False


def test_funnel_adoption_and_reply_rates(bridge_pkg, cal_db, monkeypatch):
    cal = cal_db
    pta = bridge_pkg.prior_touch_allowlist
    monkeypatch.setattr(
        pta,
        "is_prior_touch_allowlisted",
        lambda *, handle=None, email=None: str(handle or "").lstrip("@") == "old_collab",
    )
    cid = "FUNNEL-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")

    iid_old = cal.upsert_identity(
        primary_handle="@old_collab",
        primary_email="old@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_old, source="discovery", env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid_old, days=20)

    iid_draft = cal.upsert_identity(
        primary_handle="@funnel_draft",
        primary_email="draft@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_draft, source="discovery", env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid_draft, days=20)
    cal.write_event(
        identity_id=iid_draft,
        campaign_id=cid,
        event_type="outbound_draft_created",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    _backdate_events(cal, identity_id=iid_draft, days_ago=18)

    iid_reply = cal.upsert_identity(
        primary_handle="@funnel_reply",
        primary_email="reply@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_reply, source="discovery", env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid_reply, days=20)
    cal.write_event(
        identity_id=iid_reply,
        campaign_id=cid,
        event_type="kol_initial_outreach_draft_ready",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_reply,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        goal="",
        lane="",
        actor="cron",
        env="LIVE",
    )
    _backdate_events(cal, identity_id=iid_reply, days_ago=18)

    iid_idle = cal.upsert_identity(
        primary_handle="@funnel_idle",
        primary_email="idle@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_idle, source="discovery", env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid_idle, days=20)

    out = cal.aggregate_kol_registry_funnel(env="LIVE", days=7)
    assert out["discovered_total"] == 4
    assert out["prior_collab_excluded"] == 1
    assert out["eligible_total"] == 3
    assert out["mature_eligible_total"] == 3
    assert out["mature_adopted_within_window_count"] == 2
    assert out["pending_mature_backlog_count"] == 1
    assert out["pending_immature_count"] == 0
    assert out["mature_draft_total"] == 2
    assert out["mature_replied_within_window_count"] == 1
    assert out["initial_outreach_draft_count"] == 2
    assert out["initial_outreach_reply_count"] == 1
    assert out["kol_candidate_adoption_rate"] == pytest.approx(2 / 3)
    assert out["initial_outreach_reply_rate"] == pytest.approx(0.5)
    assert out["funnel_window_days"] == 30


def test_funnel_adoption_excludes_immature_discoveries(bridge_pkg, cal_db):
    cal = cal_db
    cid = "FUNNEL-IMMATURE"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid_new = cal.upsert_identity(
        primary_handle="@funnel_new",
        primary_email="new@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid_new, source="discovery", env="LIVE",
    )

    out = cal.aggregate_kol_registry_funnel(env="LIVE")
    assert out["eligible_total"] == 1
    assert out["mature_eligible_total"] == 0
    assert out["pending_immature_count"] == 1
    assert out["kol_candidate_adoption_rate"] == 0.0


def test_registry_funnel_trend_returns_series(bridge_pkg, cal_db):
    cal = cal_db
    cid = "FUNNEL-TREND-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@funnel_trend",
        primary_email="trend@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid, days=20)
    cal.write_event(
        identity_id=iid,
        campaign_id=cid,
        event_type="kol_initial_outreach_draft_ready",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    _backdate_events(cal, identity_id=iid, days_ago=18)

    out = cal.aggregate_kol_registry_funnel_trend(env="LIVE", bucket="month", periods=3)
    adoption = out["series"]["kol_candidate_adoption_rate"]
    assert len(adoption) == 3
    assert any(row["value"] == 1.0 for row in adoption if row["value"] is not None)


def test_discovery_summary_counts_by_latest_status(bridge_pkg, cal_db):
    cal = cal_db
    cid_a = "SUM-CAMP-A"
    cid_b = "SUM-CAMP-B"
    cal.upsert_campaign_config(campaign_id=cid_a, env="LIVE")
    cal.upsert_campaign_config(campaign_id=cid_b, env="LIVE")

    iid_pass = cal.upsert_identity(
        primary_handle="@sum_pass",
        primary_email="pass@example.com",
        env="LIVE",
    )
    iid_pending = cal.upsert_identity(
        primary_handle="@sum_pending",
        primary_email="pending@example.com",
        env="LIVE",
    )
    iid_reject = cal.upsert_identity(
        primary_handle="@sum_reject",
        primary_email="reject@example.com",
        env="LIVE",
    )
    iid_latest = cal.upsert_identity(
        primary_handle="@sum_latest",
        primary_email="latest@example.com",
        env="LIVE",
    )

    cal.upsert_candidate(
        campaign_id=cid_a, identity_id=iid_pass, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid_a,
        identity_ids=[iid_pass],
        candidate_status="selected_for_outreach",
        env="LIVE",
    )

    cal.upsert_candidate(
        campaign_id=cid_a, identity_id=iid_pending, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid_a,
        identity_ids=[iid_pending],
        candidate_status="shortlisted",
        env="LIVE",
    )

    cal.upsert_candidate(
        campaign_id=cid_a, identity_id=iid_reject, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid_a,
        identity_ids=[iid_reject],
        candidate_status="rejected",
        env="LIVE",
    )

    cal.upsert_candidate(
        campaign_id=cid_a, identity_id=iid_latest, source="discovery", env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid_b, identity_id=iid_latest, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid_a,
        identity_ids=[iid_latest],
        candidate_status="discovered",
        env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid_b,
        identity_ids=[iid_latest],
        candidate_status="selected_for_outreach",
        env="LIVE",
    )

    out = cal.aggregate_kol_discovery_summary(env="LIVE")
    assert out["discovered_total"] == 4
    assert out["passed_count"] == 2
    assert out["pending_count"] == 1
    assert out["rejected_count"] == 1
    assert out["pass_rate"] == pytest.approx(0.5)
    assert out["initial_outreach_draft_count"] == 0
    assert out["initial_outreach_reply_count"] == 0
    assert out["initial_outreach_reply_rate"] == 0.0


def test_discovery_summary_reply_rate(bridge_pkg, cal_db):
    cal = cal_db
    cid = "SUM-REPLY-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")

    iid_draft = cal.upsert_identity(
        primary_handle="@sum_draft",
        primary_email="draft@example.com",
        env="LIVE",
    )
    iid_reply = cal.upsert_identity(
        primary_handle="@sum_reply",
        primary_email="reply@example.com",
        env="LIVE",
    )
    for iid in (iid_draft, iid_reply):
        cal.upsert_candidate(
            campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
        )
    cal.write_event(
        identity_id=iid_draft,
        campaign_id=cid,
        event_type="kol_initial_outreach_draft_ready",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_reply,
        campaign_id=cid,
        event_type="outbound_draft_created",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_reply,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        goal="",
        lane="",
        actor="cron",
        env="LIVE",
    )

    out = cal.aggregate_kol_discovery_summary(env="LIVE")
    assert out["initial_outreach_draft_count"] == 2
    assert out["initial_outreach_reply_count"] == 1
    assert out["pending_reply_count"] == 1
    assert out["initial_outreach_reply_rate"] == pytest.approx(0.5)


def test_discovery_summary_excludes_automated_replies(bridge_pkg, cal_db):
    cal = cal_db
    cid = "SUM-AUTO-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")

    iid_bounce = cal.upsert_identity(
        primary_handle="@sum_bounce",
        primary_email="bounce@example.com",
        env="LIVE",
    )
    iid_human = cal.upsert_identity(
        primary_handle="@sum_human",
        primary_email="human@example.com",
        env="LIVE",
    )
    for iid in (iid_bounce, iid_human):
        cal.upsert_candidate(
            campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
        )
        cal.write_event(
            identity_id=iid,
            campaign_id=cid,
            event_type="kol_initial_outreach_draft_ready",
            goal="outreach",
            lane="commerce",
            actor="test",
            env="LIVE",
        )
    cal.write_event(
        identity_id=iid_bounce,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="cron",
        payload={
            "from_addr": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
            "subject": "Delivery Status Notification (Failure)",
            "body": "** Address not found **",
        },
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_human,
        campaign_id=cid,
        event_type="kol_inbound_reply",
        actor="cron",
        payload={
            "from_addr": "Human <human@example.com>",
            "subject": "Re: Collab",
            "body": "Happy to chat more about this.",
        },
        env="LIVE",
    )

    out = cal.aggregate_kol_discovery_summary(env="LIVE")
    assert out["initial_outreach_draft_count"] == 2
    assert out["initial_outreach_reply_count"] == 1
    assert out["automated_reply_excluded_count"] == 1
    assert out["pending_reply_count"] == 1
    assert out["initial_outreach_reply_rate"] == pytest.approx(0.5)


def test_discovery_summary_trend_returns_cumulative_series(bridge_pkg, cal_db):
    cal = cal_db
    cid = "SUM-TREND-CAMP"
    cal.upsert_campaign_config(campaign_id=cid, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle="@sum_trend",
        primary_email="trend@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=cid, identity_id=iid, source="discovery", env="LIVE",
    )
    cal.set_candidate_status(
        campaign_id=cid,
        identity_ids=[iid],
        candidate_status="selected_for_outreach",
        env="LIVE",
    )
    _backdate_candidate(cal, campaign_id=cid, identity_id=iid, days=40)

    out = cal.aggregate_kol_discovery_summary_trend(
        env="LIVE", bucket="month", periods=3,
    )
    assert out["bucket"] == "month"
    assert len(out["series"]["discovered_total"]) == 3
    assert all("bucket" in row and "value" in row for row in out["series"]["discovered_total"])
    totals = [row["value"] for row in out["series"]["discovered_total"]]
    assert totals[-1] == pytest.approx(1.0)
    assert totals[0] <= totals[1] <= totals[2]
    pass_rates = [row["value"] for row in out["series"]["pass_rate"]]
    assert pass_rates[-1] == pytest.approx(1.0)


def test_registry_source_legacy_filter_returns_empty(bridge_pkg, cal_db):
    cal = cal_db
    iid_legacy = cal.upsert_identity(
        primary_handle="@legacy_only_kol",
        primary_email="legacy@example.com",
        env="LIVE",
    )
    legacy_cid = "legacy-redlist-test-legacy-only-kol-abc123"
    cal.write_facts(
        identity_id=iid_legacy,
        campaign_id=legacy_cid,
        namespace="identity",
        facts={"identity.legacy_import_id": "redlist:abc123"},
        source="test",
        env="LIVE",
    )
    cal.write_event(
        identity_id=iid_legacy,
        campaign_id=legacy_cid,
        event_type="legacy.collab_imported",
        goal="post_collab_archival",
        lane="meta",
        actor="test",
        env="LIVE",
    )

    legacy_out = cal.list_discovered_kol_registry(env="LIVE", source="legacy", limit=50)
    assert legacy_out["total"] == 0
    assert legacy_out["items"] == []
