"""Stage D: sliding time window for distill sampling."""

from __future__ import annotations

import datetime as _dt


def _ev(ts):
    return {"id": 1, "ts": ts, "payload": {"was_edited": True}}


def test_window_disabled_keeps_all(bridge_pkg):
    store = bridge_pkg.learning_store
    now = _dt.datetime.now(_dt.timezone.utc)
    old = (now - _dt.timedelta(days=400)).isoformat()
    evs = [_ev(old), _ev(now.isoformat())]
    assert len(store.filter_events_within_days(evs, 0)) == 2


def test_window_drops_stale(bridge_pkg):
    store = bridge_pkg.learning_store
    now = _dt.datetime.now(_dt.timezone.utc)
    old = (now - _dt.timedelta(days=100)).isoformat()
    recent = (now - _dt.timedelta(days=5)).isoformat()
    evs = [_ev(old), _ev(recent)]
    kept = store.filter_events_within_days(evs, 30)
    assert len(kept) == 1
    assert kept[0]["ts"] == recent


def test_window_keeps_unparseable_ts(bridge_pkg):
    store = bridge_pkg.learning_store
    evs = [_ev(""), _ev(None)]
    assert len(store.filter_events_within_days(evs, 30)) == 2


def test_learning_window_days_env(bridge_pkg, monkeypatch):
    store = bridge_pkg.learning_store
    monkeypatch.delenv("KOL_LEARNING_WINDOW_DAYS", raising=False)
    assert store.learning_window_days() == 90
    monkeypatch.setenv("KOL_LEARNING_WINDOW_DAYS", "45")
    assert store.learning_window_days() == 45
    monkeypatch.setenv("KOL_LEARNING_WINDOW_DAYS", "0")
    assert store.learning_window_days() == 0
    monkeypatch.setenv("KOL_LEARNING_WINDOW_DAYS", "bad")
    assert store.learning_window_days() == 0
