"""Tests for open_escalation dedup: retry after timeout must not create a duplicate."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_dedup_test"


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


def test_open_escalation_dedup_returns_existing(monkeypatch, tmp_path):
    """Second open_escalation for same session with open state returns the same id."""
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")

    cal.enqueue_session(quickcep_session_id="qs-dedup", message_id="m1", env="LIVE")
    eid1 = cal.open_escalation(
        quickcep_session_id="qs-dedup",
        reason="need photos",
        urgency="medium",
        env="LIVE",
    )
    assert eid1 is not None

    # Retry (simulating client timeout + retry)
    eid2 = cal.open_escalation(
        quickcep_session_id="qs-dedup",
        reason="need photos",
        urgency="medium",
        env="LIVE",
    )

    # Must return the SAME escalation, not create a duplicate
    assert eid2 == eid1

    # Verify only one escalation row exists
    escs = cal.list_escalations_for_session(
        quickcep_session_id="qs-dedup", env="LIVE"
    )
    assert len(escs) == 1


def test_open_escalation_after_resolve_creates_new(monkeypatch, tmp_path):
    """After an escalation is resolved, a new one can be created for the same session."""
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")

    cal.enqueue_session(quickcep_session_id="qs-reopen", message_id="m1", env="LIVE")
    eid1 = cal.open_escalation(
        quickcep_session_id="qs-reopen",
        reason="first issue",
        urgency="low",
        env="LIVE",
    )
    assert eid1 is not None

    # Resolve the first escalation
    cal.resolve_escalation(
        escalation_id=eid1, decision="resolved", decided_by="test",
    )

    # Now a new escalation should be allowed (not deduped)
    eid2 = cal.open_escalation(
        quickcep_session_id="qs-reopen",
        reason="second issue",
        urgency="medium",
        env="LIVE",
    )
    assert eid2 is not None
    assert eid2 != eid1

    # Two escalation rows exist
    escs = cal.list_escalations_for_session(
        quickcep_session_id="qs-reopen", env="LIVE"
    )
    assert len(escs) == 2
