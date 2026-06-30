"""Tests for Feishu operator-manual-reply completion copy."""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_feishu_outcome_test"


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


def test_notify_operator_manual_reply_copy():
    mod = _load("feishu_notify")
    captured: dict = {}

    @dataclass
    class _R:
        ok: bool = True
        message_id: str = "m1"

    def _send(*, chat_id, text):
        captured["text"] = text
        return _R()

    with patch.object(mod, "send_group_text", side_effect=_send):
        with patch.object(mod, "escalation_chat_id", return_value="chat-1"):
            mod.notify_escalation_completed(
                escalation_id=42,
                quickcep_session_id="sess-1",
                outcome="operator_manual_reply",
                operator_hint="人工已回复",
            )

    assert "直接回复" in captured["text"]
    assert "草稿待审" not in captured["text"]
