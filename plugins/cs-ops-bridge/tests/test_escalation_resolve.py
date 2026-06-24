"""Tests for operational escalation resolve."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_resolve_test"


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


def test_resolve_resuming_uses_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resolve_mod = _load("escalation_resolve")

    cal.enqueue_session(quickcep_session_id="qs-res", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-res", reason="test", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="answer",
        decided_by="op",
        feishu_reply_message_id="om1",
    )

    with patch.object(
        resolve_mod,
        "complete_resuming_escalation_by_id",
        return_value={"ok": True, "escalation_id": eid, "outcome": "draft_ready"},
    ) as complete:
        out = resolve_mod.resolve_escalation_operational(
            escalation_id=eid,
            decision="manual_close",
            decided_by="console",
        )

    assert out["ok"] is True
    complete.assert_called_once()
    assert complete.call_args.kwargs["phase"] == "draft_ready"


def test_resolve_resuming_failed_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load("cal")
    resolve_mod = _load("escalation_resolve")

    cal.enqueue_session(quickcep_session_id="qs-res2", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-res2", reason="test", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="answer",
        decided_by="op",
        feishu_reply_message_id="om1",
    )

    with patch.object(
        resolve_mod,
        "complete_resuming_escalation_by_id",
        return_value={"ok": True, "escalation_id": eid, "outcome": "failed"},
    ) as complete:
        resolve_mod.resolve_escalation_operational(
            escalation_id=eid,
            decision="cancelled",
            decided_by="console",
        )

    assert complete.call_args.kwargs["phase"] == "failed"
