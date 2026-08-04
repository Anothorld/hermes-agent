"""Tests for the pre_tool_call hook quota reset behavior."""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_INTERNAL = _PLUGIN / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

# Do NOT add _PLUGIN to sys.path — it contains tools.py which would shadow
# the hermes-core ``tools`` package that hooks.py's sibling tools.py imports.

import importlib.util

# Load hooks via importlib (same pattern as __init__.py)
_spec = importlib.util.spec_from_file_location("kdr_hooks_test", _PLUGIN / "hooks.py")
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


def test_quota_reset_on_new_turn_id(monkeypatch):
    """Same task_id with a new turn_id (rediscover) must reset pacing quota."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    tid = "kol-campaign:LIVE:SEB8008-20260525"
    hooks.pre_tool_call(
        "rpa_fetch_ig_profile",
        {"handle": "x"},
        task_id=tid,
        turn_id="session:task:aaaa1111",
    )
    assert reset_calls == [tid]

    # Same turn — no second reset
    hooks.pre_tool_call(
        "rpa_fetch_ig_reels",
        {"handle": "x"},
        task_id=tid,
        turn_id="session:task:aaaa1111",
    )
    assert reset_calls == [tid]

    # New turn (rediscover / auto-retry) — must reset again
    hooks.pre_tool_call(
        "rpa_precheck_handle",
        {"handle": "y"},
        task_id=tid,
        turn_id="session:task:bbbb2222",
    )
    assert reset_calls == [tid, tid]


def test_quota_reset_on_new_task_id(monkeypatch):
    """First RPA call for a new task_id should reset pacing quota."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    hooks.pre_tool_call(
        "rpa_fetch_ig_profile",
        {"handle": "x"},
        task_id="kol-campaign:LIVE:NEW",
        turn_id="t1",
    )
    assert reset_calls == ["kol-campaign:LIVE:NEW"]

    hooks.pre_tool_call(
        "rpa_fetch_ig_reels",
        {"handle": "x"},
        task_id="kol-campaign:LIVE:NEW",
        turn_id="t1",
    )
    assert reset_calls == ["kol-campaign:LIVE:NEW"]

    hooks.pre_tool_call(
        "rpa_precheck_handle",
        {"handle": "y"},
        task_id="kol-campaign:LIVE:OTHER",
        turn_id="t2",
    )
    assert reset_calls == ["kol-campaign:LIVE:NEW", "kol-campaign:LIVE:OTHER"]


def test_legacy_empty_turn_id_resets_once(monkeypatch):
    """Without turn_id, keep one-shot reset per task_id (no per-call churn)."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    hooks.pre_tool_call("rpa_check_ip", {}, task_id="legacy-task")
    hooks.pre_tool_call("rpa_check_ip", {}, task_id="legacy-task")
    assert reset_calls == ["legacy-task"]


def test_quota_reset_clears_download_counter(monkeypatch):
    """New turn should also clear per-run reel download tracking."""
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})
    monkeypatch.setitem(
        sys.modules,
        "pacing",
        type("P", (), {"reset": staticmethod(lambda tid: None)})(),
    )

    tid = "kol-campaign:LIVE:X"
    hooks._download_reels[tid] = ["https://instagram.com/reel/a/"]
    assert hooks.maybe_reset_run_quota(tid, "turn-1") is True
    assert tid not in hooks._download_reels
    assert hooks.maybe_reset_run_quota(tid, "turn-1") is False
    assert hooks.maybe_reset_run_quota(tid, "turn-2") is True


def test_quota_reset_only_for_rpa_tools(monkeypatch):
    """Non-RPA tools should not trigger quota reset."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    hooks.pre_tool_call("terminal", {"command": "ls"}, task_id="some-task", turn_id="t1")
    assert reset_calls == []


def test_quota_reset_failure_does_not_block(monkeypatch):
    """If pacing.reset() raises, the tool call should still proceed."""
    monkeypatch.setattr(hooks, "_quota_epoch_by_task", {})

    class BrokenPacing:
        @staticmethod
        def reset(tid):
            raise RuntimeError("pacing module broken")

    monkeypatch.setitem(sys.modules, "pacing", BrokenPacing)

    result = hooks.pre_tool_call(
        "rpa_fetch_ig_profile",
        {"handle": "x"},
        task_id="t1",
        turn_id="turn-x",
    )
    assert result is None
