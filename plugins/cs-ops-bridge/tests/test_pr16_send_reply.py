"""Tests for PR1.6: send_reply (service-initiated send via scoped subprocess env)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr16_test"


def _load_pkg_module(sub: str):
    if _PKG not in sys.modules:
        import types

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


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal16.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_pkg_module("cal")


@pytest.fixture()
def send_module(cal):
    cal.enqueue_session(
        quickcep_session_id="qc-send",
        customer_email="a@b.com",
        message_id="m1",
        email_subject="Re: order",
    )
    sess = cal.get_session(quickcep_session_id="qc-send")
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    cal.update_session_chat_id(session_row_id=sess["id"], chat_session_id="chat-9")
    cal.save_draft(quickcep_session_id="qc-send", draft_html="<p>reply</p>", source="agent")
    return _load_pkg_module("send_reply")


def _stub_cli(tmp_path, monkeypatch, send_module):
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(send_module, "_quickcep_cli_path", lambda: cli)
    return cli


def test_send_reply_invokes_send_email_with_scoped_env(send_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, send_module)
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=0, stdout=json.dumps({"action": "send_email", "success": True}), stderr="")

    monkeypatch.setattr(send_module.subprocess, "run", fake_run)
    monkeypatch.setattr(send_module, "_guard_draft", lambda content, att: None)
    monkeypatch.setattr(
        send_module, "fetch_messages",
        lambda *, quickcep_session_id: {"messages": [{"id": "outbound-1"}]},
    )
    monkeypatch.setattr(send_module, "handle_operator_send", lambda info, env=None: {"ok": True})

    res = send_module.send_reply(
        quickcep_session_id="qc-send",
        operator_id="op-7",
        operator_name="Alice",
    )
    assert res["ok"] is True
    assert res["message_id"] == "outbound-1"
    # The subprocess env must carry the service send override (guard bypass).
    assert captured["env"]["CS_OPS_ALLOW_QUICKCEP_SEND"] == "1"
    # And the original environment is preserved (inherited), not wiped.
    assert "PATH" in captured["env"]
    # CLI invoked with send-email + the stored draft body + subject.
    assert "send-email" in captured["argv"]
    assert "qc-send" in captured["argv"]
    assert "<p>reply</p>" in captured["argv"]
    assert "Re: order" in captured["argv"]


def test_send_reply_no_draft_returns_error(send_module, monkeypatch):
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-nodraft", customer_email="b@c.com", message_id="m1")
    res = send_module.send_reply(quickcep_session_id="qc-nodraft")
    assert res["ok"] is False
    assert res["error"] == "no_draft"


def test_send_reply_unknown_session(send_module):
    res = send_module.send_reply(quickcep_session_id="nope")
    assert res["ok"] is False
    assert res["error"] == "session not found"


def test_send_reply_send_failure_returns_error(send_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, send_module)
    monkeypatch.setattr(send_module, "_guard_draft", lambda content, att: None)
    monkeypatch.setattr(
        send_module.subprocess, "run",
        lambda argv, **kwargs: MagicMock(returncode=2, stdout="", stderr="send blocked"),
    )
    res = send_module.send_reply(quickcep_session_id="qc-send")
    assert res["ok"] is False
    assert res["error"] == "send_failed"
    assert res["exit_code"] == 2


def test_send_reply_guard_block_returns_error(send_module, monkeypatch):
    monkeypatch.setattr(
        send_module, "_guard_draft",
        lambda content, att: {"blocked": True, "error": "internal domain", "matches": ["oss.internal"]},
    )
    res = send_module.send_reply(quickcep_session_id="qc-send")
    assert res["ok"] is False
    assert res["error"] == "guard_blocked"


def test_send_reply_records_audit_event(send_module, monkeypatch, tmp_path):
    _stub_cli(tmp_path, monkeypatch, send_module)
    monkeypatch.setattr(send_module, "_guard_draft", lambda content, att: None)
    monkeypatch.setattr(
        send_module.subprocess, "run",
        lambda argv, **kwargs: MagicMock(returncode=0, stdout='{"success":true}', stderr=""),
    )
    monkeypatch.setattr(
        send_module, "fetch_messages",
        lambda *, quickcep_session_id: {"messages": [{"id": "out-2"}]},
    )
    monkeypatch.setattr(send_module, "handle_operator_send", lambda info, env=None: {"ok": True})

    send_module.send_reply(
        quickcep_session_id="qc-send", operator_id="op-7", operator_name="Alice",
    )
    cal = _load_pkg_module("cal")
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM cs_conversation_events "
            "WHERE event_type='operator_draft_sent' ORDER BY id DESC LIMIT 1",
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["operator_id"] == "op-7"
    assert payload["operator_name"] == "Alice"
    assert payload["message_id"] == "out-2"
    assert payload["attachments"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
