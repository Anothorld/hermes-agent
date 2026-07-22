"""Tests for POST /escalations Feishu dedup-skip on idempotent retry.

Reproduces the ESC:339/340 failure mode: the agent times out on open-escalation
after the bridge already delivered the Feishu message, then retries. CAL dedups
the escalation row; the route must additionally skip the re-send (which would
post a duplicate group message) and return the existing thread ids so the agent
can still apply-handoff --feishu-thread-id.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_esc_feishu_dedup_test"


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


def _make_body(api, **overrides):
    base = dict(
        quickcep_session_id="qs-esc-feishu",
        reason="need assembly video",
        urgency="medium",
        customer_email="tshea2121@gmail.com",
        email_summary="客户需要 SF8181 安装说明。",
        email_quote="Please send the SF8181 assembly instructions.",
        env="LIVE",
    )
    base.update(overrides)
    return api.EscalationOpenBody(**base)


def test_retry_after_successful_send_skips_resend(monkeypatch, tmp_path):
    """Deduped retry where the first send succeeded must NOT re-post to Feishu."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    _load("feishu_client")
    _load("feishu_notify")
    api = _load("plugin_api")
    notify = sys.modules[f"{_PKG}.feishu_notify"]

    cal.enqueue_session(quickcep_session_id="qs-esc-feishu", message_id="m1", env="LIVE")

    fake_ok = notify.FeishuSendResult(
        ok=True, message_id="om_msg_1", thread_id="om_msg_1", chat_id="oc_chat"
    )

    with patch.object(api, "_require_bridge_key"), \
         patch.object(notify, "validate_feishu_notify_inputs", return_value=None), \
         patch.object(notify, "notify_escalation_opened", return_value=fake_ok) as mocked_send:
        r1 = api.open_escalation(_make_body(api), x_bridge_key="test-key")
        assert r1["feishu"]["ok"] is True
        assert r1["feishu"]["message_id"] == "om_msg_1"
        assert mocked_send.call_count == 1

        # Retry — agent timed out, retries the same open-escalation.
        r2 = api.open_escalation(_make_body(api), x_bridge_key="test-key")

    # Same escalation id (CAL dedup)
    assert r2["escalation_id"] == r1["escalation_id"]
    # Feishu send NOT called again
    assert mocked_send.call_count == 1
    # Existing thread info returned so agent can apply-handoff
    assert r2["feishu"]["ok"] is True
    assert r2["feishu"]["thread_id"] == "om_msg_1"
    assert r2["feishu"]["message_id"] == "om_msg_1"
    assert r2["feishu"]["dedup_skipped"] is True
    # Only one escalation row
    escs = cal.list_escalations_for_session(
        quickcep_session_id="qs-esc-feishu", env="LIVE"
    )
    assert len(escs) == 1


def test_retry_after_failed_send_still_sends(monkeypatch, tmp_path):
    """When the first send failed (no message_id persisted), retry must resend."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    _load("cal")
    _load("feishu_client")
    _load("feishu_notify")
    api = _load("plugin_api")
    notify = sys.modules[f"{_PKG}.feishu_notify"]
    cal = sys.modules[f"{_PKG}.cal"]

    cal.enqueue_session(quickcep_session_id="qs-esc-feishu", message_id="m1", env="LIVE")

    fail = notify.FeishuSendResult(ok=False, error="feishu 500")
    ok = notify.FeishuSendResult(
        ok=True, message_id="om_msg_2", thread_id="om_msg_2", chat_id="oc_chat"
    )

    sends = [fail, ok]

    def fake_send(**kwargs):
        return sends.pop(0)

    with patch.object(api, "_require_bridge_key"), \
         patch.object(notify, "validate_feishu_notify_inputs", return_value=None), \
         patch.object(notify, "notify_escalation_opened", side_effect=fake_send) as mocked_send:
        r1 = api.open_escalation(_make_body(api), x_bridge_key="test-key")
        assert r1["feishu"]["ok"] is False
        # First send failed → update_escalation_feishu not called → no message_id persisted
        assert mocked_send.call_count == 1

        r2 = api.open_escalation(_make_body(api), x_bridge_key="test-key")

    # Same escalation id (CAL dedup), but send retried because no message_id was persisted
    assert r2["escalation_id"] == r1["escalation_id"]
    assert mocked_send.call_count == 2
    assert r2["feishu"]["ok"] is True
    assert r2["feishu"]["message_id"] == "om_msg_2"
    assert "dedup_skipped" not in r2["feishu"]


def test_explicit_thread_ids_skip_send(monkeypatch, tmp_path):
    """Caller-supplied feishu_message_id + feishu_thread_id skips send (pre-existing)."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_BRIDGE_KEY", "test-key")
    _load("cal")
    _load("feishu_client")
    _load("feishu_notify")
    api = _load("plugin_api")
    notify = sys.modules[f"{_PKG}.feishu_notify"]
    cal = sys.modules[f"{_PKG}.cal"]

    cal.enqueue_session(quickcep_session_id="qs-esc-feishu", message_id="m1", env="LIVE")

    with patch.object(api, "_require_bridge_key"), \
         patch.object(notify, "notify_escalation_opened") as mocked_send:
        r = api.open_escalation(
            _make_body(
                api,
                feishu_message_id="om_pre",
                feishu_thread_id="om_pre",
            ),
            x_bridge_key="test-key",
        )

    assert mocked_send.call_count == 0
    assert r["feishu"]["skipped"] is True
