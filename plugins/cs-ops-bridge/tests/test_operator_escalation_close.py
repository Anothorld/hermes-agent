"""Tests for escalation close on manual operator send."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_op_esc_close_test"


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


def test_closes_awaiting_answer_without_touching_session(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true")
    cal = _load("cal")
    close_mod = _load("operator_escalation_close")

    r = cal.enqueue_session(quickcep_session_id="qs-esc", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="awaiting_expert")
    eid = cal.open_escalation(quickcep_session_id="qs-esc", reason="need expert", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="operator_replied")

    out = close_mod.close_escalations_on_operator_manual_reply(
        quickcep_session_id="qs-esc",
        env="LIVE",
    )

    assert len(out["closed"]) == 1
    assert out["closed"][0]["escalation_id"] == eid
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resolved"
    assert esc["decision"] == "operator_manual_reply"
    assert cal.get_session(quickcep_session_id="qs-esc", env="LIVE")["status"] == "operator_replied"


def test_handle_operator_send_closes_open_escalation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true")
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(
        quickcep_session_id="qs-send",
        message_id="m1",
        env="LIVE",
        chat_session_id="c1",
    )
    cal.update_session_status(session_row_id=r["session"]["id"], status="awaiting_expert")
    eid = cal.open_escalation(quickcep_session_id="qs-send", reason="need expert", env="LIVE")

    with patch.object(sh, "apply_handoff", return_value={"ok": True}) as handoff:
        out = sh.handle_operator_send(
            {"chatSubSessionId": "qs-send", "id": "op-msg-1", "channel": "email"},
            env="LIVE",
        )

    assert out["ok"] is True
    handoff.assert_called_once()
    esc = cal.get_escalation(escalation_id=eid)
    assert esc["state"] == "resolved"
    assert "escalation_close" in out


def test_dedup_still_closes_orphan_escalation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true")
    cal = _load("cal")
    sh = _load("session_handoff")

    r = cal.enqueue_session(quickcep_session_id="qs-dedup", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="operator_replied")
    eid = cal.open_escalation(quickcep_session_id="qs-dedup", reason="need expert", env="LIVE")
    cal.write_facts(
        quickcep_session_id="qs-dedup",
        namespaces={"handoff": {"last_operator_outbound_id": "op-same"}},
        env="LIVE",
    )

    out = sh.handle_operator_send(
        {"chatSubSessionId": "qs-dedup", "id": "op-same", "channel": "email"},
        env="LIVE",
    )

    assert out.get("skipped") is True
    assert out.get("reason") == "deduped message id"
    assert cal.get_escalation(escalation_id=eid)["state"] == "resolved"
    assert "escalation_close" in out


def test_repair_orphaned_escalations(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true")
    cal = _load("cal")
    close_mod = _load("operator_escalation_close")

    r = cal.enqueue_session(quickcep_session_id="qs-repair", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="operator_replied")
    eid = cal.open_escalation(quickcep_session_id="qs-repair", reason="stuck", env="LIVE")
    cal.write_event(
        quickcep_session_id="qs-repair",
        event_type="operator_sent",
        payload={"message_id": "op-1"},
        env="LIVE",
    )

    stats = close_mod.repair_orphaned_escalations_once(env="LIVE")

    assert stats["repaired"] == 1
    assert cal.get_escalation(escalation_id=eid)["state"] == "resolved"
