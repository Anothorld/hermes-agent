"""Tests for REST operator_sent backfill."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_op_reconcile_test"


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


def test_reconcile_operator_sent_syncs_draft_ready(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    rec = _load("operator_send_reconcile")

    r = cal.enqueue_session(
        quickcep_session_id="2547506973813489665",
        message_id="m1",
        env="LIVE",
        customer_email="jessicahall289@gmail.com",
    )
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")

    op_msg = {"id": "2547621894254018560", "createTime": "2026-06-24 17:32:08"}

    class _Proc:
        returncode = 0
        stdout = json.dumps({"messages": [{"ownerType": "operator", "id": op_msg["id"]}]})
        stderr = ""

    with patch.object(rec, "_fetch_last_operator_message", return_value=op_msg):
        with patch.object(rec, "handle_operator_send", return_value={"ok": True}) as handoff:
            stats = rec.reconcile_operator_sent_once(env="LIVE")

    assert stats["synced"] == 1
    handoff.assert_called_once()


def test_reconcile_skips_when_visitor_is_latest(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    rec = _load("operator_send_reconcile")
    detect = _load("operator_outbound_detect")

    r = cal.enqueue_session(quickcep_session_id="2547506973813489667", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")

    messages = [
        {"ownerType": "visitor", "contentType": "html", "id": "v-new"},
        {"ownerType": "operator", "contentType": "html", "id": "op-old"},
    ]
    assert detect.pick_latest_operator_outbound_email(messages) is None

    with patch.object(rec, "_fetch_last_operator_message", return_value=None):
        with patch.object(rec, "handle_operator_send") as handoff:
            stats = rec.reconcile_operator_sent_once(env="LIVE")

    assert stats["synced"] == 0
    handoff.assert_not_called()


def test_reconcile_skips_when_operator_sent_event_exists(monkeypatch, tmp_path):
    """Same-cycle operator_sent (after inbound) still skips QuickCEP fetch."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    rec = _load("operator_send_reconcile")

    r = cal.enqueue_session(quickcep_session_id="2547506973813489668", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")
    # enqueue_session already wrote inbound_received; this send is same-cycle.
    cal.write_event(
        quickcep_session_id="2547506973813489668",
        event_type="operator_sent",
        payload={"message_id": "op-1"},
        env="LIVE",
    )

    with patch.object(rec, "_fetch_last_operator_message", return_value=None) as fetch:
        with patch.object(rec, "repair_orphaned_escalations_once", return_value={"checked": 0, "repaired": 0}) as repair:
            stats = rec.reconcile_operator_sent_once(env="LIVE")

    assert stats["synced"] == 0
    assert stats["skipped_already"] == 1
    fetch.assert_not_called()
    repair.assert_called_once()


def test_reconcile_checks_again_after_reopen_despite_prior_operator_sent(monkeypatch, tmp_path):
    """Prior-cycle operator_sent must not skip reconcile after customer reopen."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    rec = _load("operator_send_reconcile")

    r = cal.enqueue_session(quickcep_session_id="2547506973813489669", message_id="m1", env="LIVE")
    cal.write_event(
        quickcep_session_id="2547506973813489669",
        event_type="operator_sent",
        payload={"message_id": "op-old"},
        env="LIVE",
    )
    # Customer reopen → new inbound cycle while still draft_ready for reconcile scan.
    cal.enqueue_session(quickcep_session_id="2547506973813489669", message_id="m2", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")

    op_msg = {"id": "op-new", "createTime": "2026-07-09 12:00:00"}
    with patch.object(rec, "_fetch_last_operator_message", return_value=op_msg) as fetch:
        with patch.object(rec, "handle_operator_send", return_value={"ok": True}) as handoff:
            with patch.object(
                rec, "repair_orphaned_escalations_once", return_value={"checked": 0, "repaired": 0}
            ):
                stats = rec.reconcile_operator_sent_once(env="LIVE")

    assert stats["skipped_already"] == 0
    assert stats["synced"] == 1
    fetch.assert_called_once()
    handoff.assert_called_once()


def test_reconcile_skips_invalid_session_id_without_calling_quickcep(monkeypatch, tmp_path):
    """Non-numeric quickcep_session_id (test fixture leak) must not hit QuickCEP API."""
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    rec = _load("operator_send_reconcile")

    r = cal.enqueue_session(quickcep_session_id="sess-1", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="draft_ready")

    with patch.object(rec, "_fetch_last_operator_message") as fetch:
        with patch.object(rec, "handle_operator_send") as handoff:
            with patch.object(
                rec, "repair_orphaned_escalations_once", return_value={"checked": 0, "repaired": 0}
            ):
                stats = rec.reconcile_operator_sent_once(env="LIVE")

    assert stats["synced"] == 0
    fetch.assert_not_called()  # guard must short-circuit before the QuickCEP call
    handoff.assert_not_called()
