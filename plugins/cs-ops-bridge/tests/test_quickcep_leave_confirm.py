"""Tests for QuickCEP leave-chat confirmation (email leaveChat vs live chat_end)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_leave_confirm_test"


def _load():
    if _PKG not in sys.modules:
        import types

        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.quickcep_leave_confirm"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "quickcep_leave_confirm.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_message_content_indicates_closed():
    mod = _load()
    assert mod.message_content_indicates_closed('{"action":"chat_end","leaveChatBy":"operator"}')
    assert mod.message_content_indicates_closed('{"action":"leaveChat","content":"Arnold"}')
    assert not mod.message_content_indicates_closed('{"action":"joinChat","content":"Arnold"}')


def test_reconcile_leave_chat_payload_upgrades_email_close(monkeypatch, tmp_path):
    mod = _load()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")

    def fake_run(argv, **kwargs):
        return type(
            "P",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "messages": [
                            {"content": '{"action":"leaveChat","content":"Arnold"}'},
                        ]
                    }
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod.reconcile_leave_chat_payload(
        {"ok": False, "result_code": 200, "error": "chat_end_not_confirmed"},
        cli=cli,
        session_id="qc-1",
    )
    assert out["ok"] is True
    assert out["confirmed_via"] == "leaveChat_message"
