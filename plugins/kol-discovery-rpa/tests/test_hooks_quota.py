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


def test_quota_reset_on_new_task_id(monkeypatch):
    """First RPA call for a new task_id should reset pacing quota."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_seen_task_ids", set())

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    # First call for a new task_id — should trigger reset
    hooks.pre_tool_call("rpa_fetch_ig_profile", {"handle": "x"}, task_id="kol-campaign:LIVE:NEW")
    assert reset_calls == ["kol-campaign:LIVE:NEW"]

    # Second call for the same task_id — should NOT trigger reset again
    hooks.pre_tool_call("rpa_fetch_ig_reels", {"handle": "x"}, task_id="kol-campaign:LIVE:NEW")
    assert reset_calls == ["kol-campaign:LIVE:NEW"]  # still only 1

    # Call for a different task_id — should trigger reset
    hooks.pre_tool_call("rpa_precheck_handle", {"handle": "y"}, task_id="kol-campaign:LIVE:OTHER")
    assert reset_calls == ["kol-campaign:LIVE:NEW", "kol-campaign:LIVE:OTHER"]


def test_quota_reset_only_for_rpa_tools(monkeypatch):
    """Non-RPA tools should not trigger quota reset."""
    reset_calls: list[str] = []
    monkeypatch.setattr(hooks, "_seen_task_ids", set())

    class FakePacing:
        @staticmethod
        def reset(tid):
            reset_calls.append(tid)

    monkeypatch.setitem(sys.modules, "pacing", FakePacing)

    hooks.pre_tool_call("terminal", {"command": "ls"}, task_id="some-task")
    assert reset_calls == []  # no reset for non-RPA tools


def test_quota_reset_failure_does_not_block(monkeypatch):
    """If pacing.reset() raises, the tool call should still proceed."""
    monkeypatch.setattr(hooks, "_seen_task_ids", set())

    class BrokenPacing:
        @staticmethod
        def reset(tid):
            raise RuntimeError("pacing module broken")

    monkeypatch.setitem(sys.modules, "pacing", BrokenPacing)

    # Should not raise — best-effort reset
    result = hooks.pre_tool_call("rpa_fetch_ig_profile", {"handle": "x"}, task_id="t1")
    assert result is None  # tool call allowed
