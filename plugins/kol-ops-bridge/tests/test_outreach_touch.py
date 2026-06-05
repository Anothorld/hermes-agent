"""Tests for cross-campaign outreach touch / discovery cooldown."""

from __future__ import annotations

import datetime as _dt

import pytest


def _seed_identity(cal, handle: str = "creator_a", *, env: str = "LIVE") -> int:
    iid = cal.upsert_identity(primary_handle=handle, platform="instagram", env=env)
    assert iid is not None
    return int(iid)


def test_within_cooldown_uses_14_day_window(bridge_pkg):
    ot = bridge_pkg.outreach_touch
    recent = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).isoformat()
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).isoformat()
    assert ot.within_outreach_cooldown(recent) is True
    assert ot.within_outreach_cooldown(old) is False


def test_batch_touch_picks_latest_across_campaigns(cal_db, bridge_pkg):
    cal = cal_db
    ot = bridge_pkg.outreach_touch
    iid = _seed_identity(cal)
    older = "2026-01-01T10:00:00+00:00"
    newer = "2026-06-01T10:00:00+00:00"
    cal.write_facts(
        identity_id=iid,
        campaign_id="C-old",
        namespace="offer",
        facts={"offer.outreach_sent_at": older},
        source="test",
        env="LIVE",
    )
    cal.write_facts(
        identity_id=iid,
        campaign_id="C-new",
        namespace="offer",
        facts={"offer.outreach_sent_at": newer},
        source="test",
        env="LIVE",
    )
    touches = cal.batch_global_outreach_touch([iid], env="LIVE")
    assert touches[iid]["last_touch_at"] == newer
    assert touches[iid]["last_touch_campaign_id"] == "C-new"
    assert touches[iid]["has_prior_touch"] is True
    assert ot.within_outreach_cooldown(older) is False


def test_upsert_candidate_blocked_within_cooldown(cal_db, bridge_pkg):
    cal = cal_db
    iid = _seed_identity(cal, "cool_kol")
    cal.write_event(
        identity_id=iid,
        campaign_id="C-prior",
        event_type="outreach.sent",
        goal="outreach",
        lane="commerce",
        actor="test",
        env="LIVE",
    )
    cal.upsert_campaign_config(campaign_id="C-disc", env="LIVE")
    with pytest.raises(bridge_pkg.outreach_touch.OutreachCooldownActive):
        cal.upsert_candidate(
            campaign_id="C-disc",
            identity_id=iid,
            source="discovery",
            env="LIVE",
            enforce_outreach_cooldown=True,
        )


def test_batch_touch_from_outreach_sent_fact_captured_at(cal_db, bridge_pkg):
    cal = cal_db
    iid = _seed_identity(cal, "sent_flag_only")
    captured = "2026-05-20T12:00:00+00:00"
    cal.write_facts(
        identity_id=iid,
        campaign_id="C1",
        namespace="offer",
        facts={"offer.outreach_sent": True},
        source="test",
        env="LIVE",
    )
    with cal._connect() as conn:  # noqa: SLF001
        # write_facts also emits outreach.sent at "now" — drop it so we
        # exercise the offer.outreach_sent + captured_at path only.
        conn.execute(
            "DELETE FROM kol_conversation_events WHERE identity_id=?",
            (iid,),
        )
        conn.execute(
            """UPDATE kol_facts SET captured_at=?
                WHERE identity_id=? AND campaign_id=? AND fact_key=?""",
            (captured, iid, "C1", "offer.outreach_sent"),
        )
    touches = cal.batch_global_outreach_touch([iid], env="LIVE")
    assert touches[iid]["last_touch_at"] == captured


def test_upsert_candidate_allowed_after_cooldown(cal_db):
    cal = cal_db
    iid = _seed_identity(cal, "old_touch")
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=20)).isoformat(
        timespec="seconds",
    )
    cal.write_facts(
        identity_id=iid,
        campaign_id="C-prior",
        namespace="offer",
        facts={"offer.outreach_sent_at": old},
        source="test",
        env="LIVE",
    )
    cal.upsert_campaign_config(campaign_id="C-disc", env="LIVE")
    cid = cal.upsert_candidate(
        campaign_id="C-disc",
        identity_id=iid,
        source="discovery",
        env="LIVE",
        enforce_outreach_cooldown=True,
    )
    assert cid is not None
