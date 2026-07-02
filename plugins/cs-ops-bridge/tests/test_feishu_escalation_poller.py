"""Tests for Feishu escalation reply listing helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_feishu_poller_test"


def _load():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.feishu_escalation_poller"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "feishu_escalation_poller.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_is_topic_thread_id():
    mod = _load()
    assert mod._is_topic_thread_id("omt_abc") is True
    assert mod._is_topic_thread_id("om_x100b6c940f87ad04c35f4a536f69cc7") is False


def test_replies_to_root_filters_by_parent_id():
    mod = _load()
    root = "om_root"
    messages = [
        {"message_id": root, "parent_id": None, "body": {"content": '{"text":"[ESC:1] root"}'}},
        {"message_id": "om_reply", "parent_id": root, "body": {"content": '{"text":"operator answer"}'}},
        {"message_id": "om_other", "parent_id": "om_else", "body": {"content": '{"text":"noise"}'}},
    ]
    replies = mod._replies_to_root(messages, root)
    assert len(replies) == 1
    assert replies[0]["message_id"] == "om_reply"


def test_pick_first_operator_reply_uses_earliest():
    mod = _load()
    root = "om_root"
    messages = [
        {
            "message_id": "om_late",
            "parent_id": root,
            "create_time": "2000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"second"}'},
        },
        {
            "message_id": "om_early",
            "parent_id": root,
            "create_time": "1000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"first wins"}'},
        },
    ]
    picked = mod._pick_first_operator_reply(messages, esc_created_ms=0, seen_message_id="")
    assert picked is not None
    assert picked["message_id"] == "om_early"


def test_pick_first_skips_system_messages():
    mod = _load()
    messages = [
        {
            "message_id": "om_lock",
            "parent_id": "om_root",
            "create_time": "1000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"[ESC-LOCK:1] locked"}'},
        },
    ]
    assert mod._pick_first_operator_reply(messages, esc_created_ms=0, seen_message_id="") is None


def test_collect_operator_replies_excludes_winning_and_system():
    mod = _load()
    messages = [
        {
            "message_id": "om_win",
            "create_time": "1000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"first"}'},
        },
        {
            "message_id": "om_late",
            "create_time": "2000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"too late"}'},
        },
        {
            "message_id": "om_lock",
            "create_time": "3000",
            "sender": {"sender_type": "user"},
            "body": {"content": '{"text":"[ESC-LOCK:1] locked"}'},
        },
    ]
    replies = mod._collect_operator_replies(messages, esc_created_ms=0)
    assert [m["message_id"] for m in replies] == ["om_win", "om_late"]


def test_list_chat_messages_since_stops_at_cutoff(monkeypatch):
    mod = _load()
    pages = [
        [
            {"message_id": "m3", "create_time": "3000", "parent_id": "om_root"},
            {"message_id": "m2", "create_time": "2000", "parent_id": "om_noise"},
        ],
        [
            {"message_id": "m1", "create_time": "1000", "parent_id": "om_root"},
        ],
    ]

    def _fake_since(**kwargs):
        idx = _fake_since.calls
        _fake_since.calls += 1
        if idx < len(pages):
            return pages[idx], idx + 1
        return [], idx + 1

    _fake_since.calls = 0
    monkeypatch.setattr(mod, "list_container_messages_since", _fake_since)

    messages, fetched = mod._list_chat_messages(token="tok", chat_id="chat", since_ms=1500)
    assert fetched == 1
    assert [m["message_id"] for m in messages] == ["m3", "m2"]


def test_ensure_lock_notified_is_idempotent(monkeypatch):
    mod = _load()
    calls: list[int] = []

    class _Lock:
        ok = True
        message_id = "om_lock_msg"

    def _fake_notify(**kwargs):
        calls.append(kwargs["escalation_id"])
        return _Lock()

    def _fake_merge(**kwargs):
        return True

    monkeypatch.setattr(mod, "notify_escalation_locked", _fake_notify)
    monkeypatch.setattr(mod.cal, "merge_escalation_resume_context", _fake_merge)

    ctx: dict = {}
    assert mod._ensure_escalation_lock_notified(
        escalation_id=7,
        feishu_root_message_id="om_root",
        token="tok",
        resume_context=ctx,
    )
    assert mod._ensure_escalation_lock_notified(
        escalation_id=7,
        feishu_root_message_id="om_root",
        token="tok",
        resume_context=ctx,
    )
    assert calls == [7]
    assert ctx["feishu_lock_notified"] is True
