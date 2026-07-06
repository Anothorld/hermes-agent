"""Unit tests for the shared quickcep_join helper."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_qj_test"


def _reset_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG or key.startswith(f"{_PKG}."):
            del sys.modules[key]


def _load(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def _ok_stdout() -> str:
    return json.dumps({"action": "join_chat", "result_code": 200})


def _timeout_stdout(step: str = "joinChat") -> str:
    return json.dumps(
        {
            "error": "<urlopen error timed out>",
            "failed_step": step,
            "command": "join-chat",
        }
    )


def _proc(returncode: int, stdout: str) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


# ── success path ────────────────────────────────────────────────────────

def test_join_success_returns_ok():
    _reset_modules()
    qj = _load("quickcep_join")
    with patch.object(qj, "_run_quickcep_cli", return_value=_proc(0, _ok_stdout())):
        result = qj.join_chat_session("sess-1", source="launch")
    assert result["ok"] is True
    assert result["source"] == "launch"
    assert result["result_code"] == 200
    assert result["attempts"] == 1
    assert result["error"] is None


# ── fail-soft (watcher / relaunch) ──────────────────────────────────────

def test_join_failure_fail_soft_returns_dict_no_raise():
    _reset_modules()
    qj = _load("quickcep_join")
    with patch.object(qj, "_run_quickcep_cli", return_value=_proc(1, _timeout_stdout("joinChat"))):
        result = qj.join_chat_session("sess-fail", source="launch", raise_on_failure=False)
    assert result["ok"] is False
    assert result["source"] == "launch"
    assert result["failed_step"] == "joinChat"
    assert result["attempts"] == 1  # launch default = 1
    assert "joinChat timed out" in result["error_detail"]


def test_join_failure_launch_default_one_attempt(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setattr(qj.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def fake_run(argv, *, timeout=130):
        calls["n"] += 1
        return _proc(1, _timeout_stdout())

    with patch.object(qj, "_run_quickcep_cli", side_effect=fake_run):
        result = qj.join_chat_session("sess-once", source="launch")
    assert calls["n"] == 1
    assert result["ok"] is False
    assert result["attempts"] == 1


def test_join_failure_relaunch_one_attempt_default():
    _reset_modules()
    qj = _load("quickcep_join")
    with patch.object(qj, "_run_quickcep_cli", return_value=_proc(1, _timeout_stdout("getUserInfo"))):
        result = qj.join_chat_session("sess-rel", source="relaunch")
    assert result["ok"] is False
    assert result["source"] == "relaunch"
    assert result["attempts"] == 1
    assert result["failed_step"] == "getUserInfo"


# ── fail-hard (legacy draft-save) ───────────────────────────────────────

def test_join_failure_fail_hard_exits():
    _reset_modules()
    qj = _load("quickcep_join")
    with patch.object(qj, "_run_quickcep_cli", return_value=_proc(1, _timeout_stdout("joinChat"))):
        with patch.object(qj.sys, "exit", side_effect=SystemExit(1)) as mock_exit, \
             patch("builtins.print") as mock_print:
            with pytest.raises(SystemExit):
                qj.join_chat_session(
                    "sess-hard",
                    source="draft_save",
                    raise_on_failure=True,
                )
    mock_exit.assert_called_once()
    printed = mock_print.call_args[0][0]
    payload = json.loads(printed)
    assert payload["ok"] is False
    assert payload["source"] == "draft_save"
    assert payload["attempts"] == 3  # draft_save default


def test_join_draft_save_retries_on_timeout_then_succeeds(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setattr(qj.time, "sleep", lambda _s: None)
    n = {"i": 0}

    def fake_run(argv, *, timeout=130):
        n["i"] += 1
        if n["i"] < 3:
            return _proc(1, _timeout_stdout())
        return _proc(0, _ok_stdout())

    with patch.object(qj, "_run_quickcep_cli", side_effect=fake_run):
        result = qj.join_chat_session("sess-retry", source="draft_save")
    assert result["ok"] is True
    assert result["attempts"] == 3
    assert n["i"] == 3


def test_join_draft_save_non_retryable_failure_no_retry(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setattr(qj.time, "sleep", lambda _s: None)
    n = {"i": 0}

    def fake_run(argv, *, timeout=130):
        n["i"] += 1
        # 500 (not a timeout) → not retryable
        return _proc(1, json.dumps({"error": "internal server error", "result_code": 500}))

    with patch.object(qj, "_run_quickcep_cli", side_effect=fake_run):
        result = qj.join_chat_session("sess-500", source="draft_save")
    assert result["ok"] is False
    assert result["attempts"] == 1
    assert n["i"] == 1


# ── env toggles ─────────────────────────────────────────────────────────

def test_join_on_launch_enabled_default_true(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.delenv("CS_OPS_JOIN_CHAT_ON_LAUNCH", raising=False)
    assert qj.join_chat_on_launch_enabled() is True


def test_join_on_launch_disabled(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setenv("CS_OPS_JOIN_CHAT_ON_LAUNCH", "0")
    assert qj.join_chat_on_launch_enabled() is False


def test_launch_join_max_attempts_override(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setenv("CS_OPS_LAUNCH_JOIN_MAX_ATTEMPTS", "3")
    assert qj.launch_join_max_attempts() == 3


def test_launch_join_max_attempts_invalid_falls_back(monkeypatch):
    _reset_modules()
    qj = _load("quickcep_join")
    monkeypatch.setenv("CS_OPS_LAUNCH_JOIN_MAX_ATTEMPTS", "abc")
    assert qj.launch_join_max_attempts() == 1


# ── record_join_chat_event ──────────────────────────────────────────────

def test_record_join_chat_event_writes_cal_event(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    cal._DB_PATH = tmp_path / "cal.db"
    qj = _load("quickcep_join")
    cal.enqueue_session(quickcep_session_id="sess-ev", message_id="m1", env="LIVE")

    join_result = {"ok": True, "source": "launch", "attempts": 1, "result_code": 200,
                   "error": None, "error_detail": None, "failed_step": None}
    qj.record_join_chat_event(
        quickcep_session_id="sess-ev",
        join_result=join_result,
        message_id="m1",
        env="LIVE",
    )

    ctx = cal.get_dispatch_context(quickcep_session_id="sess-ev", env="LIVE")
    types = [e["event_type"] for e in ctx.get("recent_events", [])]
    assert "quickcep_join_chat" in types


def test_record_join_chat_event_failure_does_not_raise(monkeypatch, tmp_path):
    _reset_modules()
    qj = _load("quickcep_join")
    cal = _load("cal")  # ensure cal is in sys.modules under the package namespace
    # cal.write_event raises — record helper must swallow
    with patch.object(cal, "write_event", side_effect=RuntimeError("boom")):
        qj.record_join_chat_event(
            quickcep_session_id="sess-x",
            join_result={"ok": False, "source": "launch", "attempts": 1,
                          "error": "x", "error_detail": None, "failed_step": None,
                          "result_code": None},
            message_id=None,
        )
