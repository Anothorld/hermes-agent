"""Tests for PR1.8: Console escalation reply (first-wins claim + resume + ESC-LOCK)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr18_test"


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
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal18.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-esc", customer_email="a@b.com", message_id="m1")
    sess = cal.get_session(quickcep_session_id="qc-esc")
    cal.update_session_status(session_row_id=sess["id"], status="awaiting_expert")
    cal.open_escalation(
        quickcep_session_id="qc-esc", reason="need expert", urgency="high",
        question_to_operator="refund policy?", feishu_message_id="fm-root-1",
    )
    return cal


def test_console_reply_claims_and_resumes(cal, monkeypatch):
    esc_mod = _load_pkg_module("escalation_resume")
    resume_kwargs: list[dict] = []
    # Stub the gateway resume launch (claim path runs first, then resume with already_claimed).
    def _fake_resume(**kwargs):
        resume_kwargs.append(kwargs)
        return {"ok": True, "run_id": "run-xyz", "escalation_id": kwargs["escalation_id"]}
    monkeypatch.setattr(esc_mod, "resume_escalation", _fake_resume)
    # Stub the Feishu [ESC-LOCK] notify by registering a fake feishu_notify
    # module under the test package so the lazy relative import resolves to it.
    import types

    notify_calls: list = []
    fake_feishu_notify = types.ModuleType(f"{_PKG}.feishu_notify")

    class _FakeLockResult:
        ok = True
        message_id = "om-lock-fake"
        error = None
    fake_feishu_notify.notify_escalation_locked = lambda *, escalation_id, feishu_root_message_id: (
        notify_calls.append((escalation_id, feishu_root_message_id)) or _FakeLockResult()
    )
    monkeypatch.setitem(sys.modules, f"{_PKG}.feishu_notify", fake_feishu_notify)

    res = esc_mod.console_reply_escalation(
        escalation_id=1,
        operator_answer="退款政策为 7 天内可退",
        operator_id="op-9",
        operator_name="Bob",
    )
    assert res["ok"] is True
    assert res["claimed"] is True
    assert res["run_id"] == "run-xyz"
    assert resume_kwargs and resume_kwargs[0]["skip_attachment_prepare"] is False
    # ESC-LOCK posted to the Feishu root message.
    assert (1, "fm-root-1") in notify_calls
    # Escalation moved to resuming.
    esc = cal.get_escalation(escalation_id=1)
    assert esc["state"] == "resuming"
    assert esc["decided_by"] == "console:op-9"
    # Console path persists feishu_lock_notified so the poller won't re-post.
    ctx = esc.get("resume_context") or {}
    assert ctx.get("feishu_lock_notified") is True
    assert ctx.get("feishu_lock_message_id") == "om-lock-fake"
    # Audit event recorded.
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT event_type FROM cs_conversation_events "
            "WHERE event_type='escalation_reply_console' LIMIT 1",
        ).fetchone()
    assert row is not None


def test_console_reply_first_wins_loses_race(cal, monkeypatch):
    esc_mod = _load_pkg_module("escalation_resume")
    # Pre-claim from the Feishu path (simulate a concurrent first reply).
    assert cal.claim_escalation_reply(
        escalation_id=1, operator_answer="feishu first", decided_by="feishu:opA",
        feishu_reply_message_id="fm-rep-1",
    ) is True

    monkeypatch.setattr(esc_mod, "resume_escalation", lambda **kw: {"ok": True})
    res = esc_mod.console_reply_escalation(
        escalation_id=1, operator_answer="console second", operator_id="op-9",
    )
    assert res["ok"] is False
    assert res["error"] == "already_claimed"
    assert "first reply wins" in res["error_detail"]


def test_console_reply_unknown_escalation(cal):
    esc_mod = _load_pkg_module("escalation_resume")
    res = esc_mod.console_reply_escalation(escalation_id=999, operator_answer="x")
    assert res["ok"] is False
    assert res["error"] == "escalation not found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
