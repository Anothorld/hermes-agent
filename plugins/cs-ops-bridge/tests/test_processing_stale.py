"""Tests for processing stale recovery."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_processing_stale_test"


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


def test_processing_stale_recovers_old_processing_session(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    stale_mod = _load("processing_stale")
    monkeypatch.setattr(stale_mod, "_STALE_MIN", 5.0)

    r = cal.enqueue_session(quickcep_session_id="qs-stale", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with cal._connect() as conn:  # noqa: SLF001 — test uses same DB helper
        conn.execute("UPDATE cs_session SET updated_at=? WHERE id=?", (old, r["session"]["id"]))
        conn.commit()

    with patch.object(stale_mod, "apply_handoff", return_value={"ok": True}) as handoff:
        stats = stale_mod.check_processing_stale_once()

    assert stats["newly_recovered"] == 1
    handoff.assert_called_once()
    assert handoff.call_args.kwargs["phase"] == "failed"
    assert handoff.call_args.kwargs["quickcep_session_id"] == "qs-stale"


def test_processing_stale_skips_fresh_processing_session(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    stale_mod = _load("processing_stale")
    monkeypatch.setattr(stale_mod, "_STALE_MIN", 15.0)

    r = cal.enqueue_session(quickcep_session_id="qs-fresh", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="processing")

    with patch.object(stale_mod, "apply_handoff") as handoff:
        stats = stale_mod.check_processing_stale_once()

    assert stats["newly_recovered"] == 0
    handoff.assert_not_called()


def test_default_stale_threshold_is_two_hours():
    _reset_modules()
    stale_mod = _load("processing_stale")
    assert stale_mod._STALE_MIN == 120.0


def test_awaiting_expert_never_recovered_by_processing_stale(monkeypatch, tmp_path):
    _reset_modules()
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    stale_mod = _load("processing_stale")
    monkeypatch.setattr(stale_mod, "_STALE_MIN", 5.0)

    r = cal.enqueue_session(quickcep_session_id="qs-expert", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="awaiting_expert")
    old = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with cal._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE cs_session SET updated_at=? WHERE id=?", (old, r["session"]["id"]))
        conn.commit()

    with patch.object(stale_mod, "apply_handoff") as handoff:
        stats = stale_mod.check_processing_stale_once()

    assert stats["newly_recovered"] == 0
    handoff.assert_not_called()
    assert cal.get_session(quickcep_session_id="qs-expert", env="LIVE")["status"] == "awaiting_expert"
