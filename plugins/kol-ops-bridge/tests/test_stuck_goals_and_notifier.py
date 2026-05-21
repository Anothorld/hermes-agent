"""Phase C fix-9 tests — stuck-goal scanner and escalation notifier wiring."""

from __future__ import annotations

import datetime as _dt
from typing import Any


CAMPAIGN = "C-stuck"


def _setup_campaign(cal, *, followup: dict | None = None):
    cal.upsert_campaign_config(
        campaign_id=CAMPAIGN,
        label="Stuck-test",
        sku_whitelist=["SKU-S"],
        deliverable_platforms=["instagram"],
        deliverable_count_per_platform=1,
        paid_ceiling=1000.0,
        contract_required=True,
        followup_intervals=followup or {},
    )


def _patch_notifier(monkeypatch, cal):
    """Capture notifier.notify calls without hitting the network."""
    captured: list[dict[str, Any]] = []
    # cal imports `from . import notifier as _notifier` lazily inside the
    # private notify helpers. We swap a fake module into sys.modules under
    # the synthetic package name used by conftest so the lazy import finds
    # it instead of the real notifier.
    import sys
    import types

    pkg = sys.modules.get("kol_ops_bridge_pkg")
    assert pkg is not None
    fake = types.ModuleType("kol_ops_bridge_pkg.notifier")

    def _fake_notify(*, kind, title, lines, ref=None, timeout=5):
        captured.append({"kind": kind, "title": title,
                         "lines": list(lines), "ref": dict(ref or {})})
        return {"sent": True}

    fake.notify = _fake_notify  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kol_ops_bridge_pkg.notifier", fake)
    monkeypatch.setattr(pkg, "notifier", fake, raising=False)
    return captured


def test_open_escalation_calls_notifier(cal_db, monkeypatch):
    cal = cal_db
    _setup_campaign(cal)
    captured = _patch_notifier(monkeypatch, cal)

    iid = cal.upsert_identity(primary_handle="stuck-user-1")
    cal.write_facts(
        identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
        facts={"offer.outreach_sent": True, "offer.interest_signal": "confirmed"},
    )
    eid = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, goal="product_selection",
        reason="kol_demands_off_whitelist", severity="high",
        question_to_operator="Allow off-whitelist SKU?",
    )
    assert eid is not None
    assert len(captured) == 1
    notif = captured[0]
    assert notif["kind"] == "escalation"
    assert str(eid) in notif["title"]
    assert notif["ref"] == {"escalation_id": eid}
    body = "\n".join(notif["lines"])
    assert "kol_demands_off_whitelist" in body
    assert "high" in body
    assert "product_selection" in body


def test_open_escalation_notifier_failure_does_not_break_write(cal_db, monkeypatch):
    """Transport errors in notifier must never block the escalation insert."""
    cal = cal_db
    _setup_campaign(cal)

    import sys
    import types
    pkg = sys.modules.get("kol_ops_bridge_pkg")
    assert pkg is not None
    fake = types.ModuleType("kol_ops_bridge_pkg.notifier")

    def _boom(**_kwargs):
        raise RuntimeError("ding webhook 503")

    fake.notify = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kol_ops_bridge_pkg.notifier", fake)
    monkeypatch.setattr(pkg, "notifier", fake, raising=False)

    iid = cal.upsert_identity(primary_handle="stuck-user-2")
    eid = cal.open_escalation(
        identity_id=iid, campaign_id=CAMPAIGN, goal="cold_outreach",
        reason="something_weird",
    )
    assert eid is not None  # escalation insert succeeded despite notifier crash


def _backdate_goal(cal, *, identity_id: int, goal: str, hours: int):
    """Force a goal_state row's updated_at to N hours ago."""
    past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    iso = past.isoformat(timespec="seconds")
    with cal._connect() as conn:
        conn.execute(
            """UPDATE kol_goal_state SET updated_at=?
                WHERE identity_id=? AND campaign_id=? AND goal=?""",
            (iso, identity_id, CAMPAIGN, goal),
        )


def test_check_stuck_goals_respects_followup_intervals(cal_db, monkeypatch):
    cal = cal_db
    _setup_campaign(cal, followup={"product_selection": 24})  # 24h threshold
    captured = _patch_notifier(monkeypatch, cal)

    iid = cal.upsert_identity(primary_handle="stuck-user-3")
    cal.write_facts(
        identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
        facts={"offer.outreach_sent": True, "offer.interest_signal": "confirmed"},
    )
    # Fresh row should not be flagged.
    fresh = cal.check_stuck_goals(env="LIVE")
    assert fresh == []
    assert captured == []

    # Backdate 48h: above the 24h threshold for product_selection.
    _backdate_goal(cal, identity_id=iid, goal="product_selection", hours=48)
    stuck = cal.check_stuck_goals(env="LIVE")
    flagged = [s for s in stuck if s["goal"] == "product_selection"
               and s["identity_id"] == iid]
    assert len(flagged) == 1
    rec = flagged[0]
    assert rec["threshold_hours"] == 24
    assert rec["age_hours"] >= 24
    assert any(c["kind"] == "info" and "product_selection" in c["title"]
               for c in captured)


def test_check_stuck_goals_default_threshold_72h(cal_db, monkeypatch):
    cal = cal_db
    _setup_campaign(cal)  # no followup_intervals → default 72h
    _patch_notifier(monkeypatch, cal)

    iid = cal.upsert_identity(primary_handle="stuck-user-4")
    cal.write_facts(
        identity_id=iid, campaign_id=CAMPAIGN, namespace="offer",
        facts={"offer.outreach_sent": True, "offer.interest_signal": "confirmed"},
    )
    # Under 72h → not flagged.
    _backdate_goal(cal, identity_id=iid, goal="product_selection", hours=50)
    assert not cal.check_stuck_goals(env="LIVE")

    # Over 72h → flagged.
    _backdate_goal(cal, identity_id=iid, goal="product_selection", hours=100)
    stuck = cal.check_stuck_goals(env="LIVE")
    assert any(s["identity_id"] == iid and s["goal"] == "product_selection"
               for s in stuck)
