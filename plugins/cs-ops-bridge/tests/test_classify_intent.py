"""Tests for cs-ops-bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_test"


def _load_pkg_module(sub: str):
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


def test_classify_logistics():
    ci = _load_pkg_module("classify_intent")
    out = ci.classify_intent(subject="Order status", body="Where is my shipment?")
    assert out["route"] == "auto_handle"
    assert out["category"] == "logistics"


def test_classify_vip_discount_escalates():
    ci = _load_pkg_module("classify_intent")
    out = ci.classify_intent(
        subject="VIP discount",
        body="I am a loyal VIP customer and need 15% discount",
    )
    assert out["route"] == "escalate"


def test_enqueue_dedup(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    r1 = cal.enqueue_session(quickcep_session_id="123", message_id="m1", env="LIVE")
    r2 = cal.enqueue_session(quickcep_session_id="123", message_id="m1", env="LIVE")
    assert r1["created"] is True
    assert r1["should_launch"] is True
    assert r2["deduped"] is True
    assert r2["should_launch"] is False


def test_write_facts_masks_pii(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="789", message_id="m1", env="LIVE")
    cal.write_facts(
        quickcep_session_id="789",
        namespaces={"customer": {"email": "secret@example.com", "phone": "415-555-9999"}},
        env="LIVE",
    )
    ctx = cal.get_dispatch_context(quickcep_session_id="789", env="LIVE")
    blob = json.dumps(ctx["facts"])
    assert "secret@example.com" not in blob
    assert "555-9999" not in blob


def test_enqueue_skips_launch_when_busy(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    r1 = cal.enqueue_session(quickcep_session_id="456", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r1["session"]["id"], status="processing")
    r2 = cal.enqueue_session(quickcep_session_id="456", message_id="m2", env="LIVE")
    assert r2["created"] is True
    assert r2["should_launch"] is False


def test_dispatch_context_prefills_tracking_for_logistics(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    ot = _load_pkg_module("order_tracking")
    cal.enqueue_session(quickcep_session_id="prefill-1", message_id="m1", env="LIVE")
    monkeypatch.setattr(
        cal,
        "_fetch_visitor_orders",
        lambda _sid: {
            "orders": [{"orderId": "260619360220455021"}],
            "intention_tags": ["物流咨询"],
            "source": "getOrderList",
        },
    )
    monkeypatch.setattr(
        ot,
        "fetch_tracking_prefill",
        lambda ids, max_orders=3: {
            "enabled": True,
            "source": "order-track-api",
            "reason": "ok",
            "circuitOpen": False,
            "summaries": [{"orderId": ids[0], "status": "transit"}],
            "errors": [],
        },
    )
    ctx = cal.get_dispatch_context(quickcep_session_id="prefill-1", env="LIVE")
    assert ctx["tracking"]["enabled"] is True
    assert ctx["tracking"]["summaries"][0]["status"] == "transit"


def test_dispatch_context_skips_tracking_for_non_logistics(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    cal = _load_pkg_module("cal")
    ot = _load_pkg_module("order_tracking")
    cal.enqueue_session(quickcep_session_id="prefill-2", message_id="m1", env="LIVE")
    monkeypatch.setattr(
        cal,
        "_fetch_visitor_orders",
        lambda _sid: {
            "orders": [{"orderId": "260619360220455021"}],
            "intention_tags": ["产品咨询"],
            "source": "getOrderList",
        },
    )

    def _should_not_run(_ids, max_orders=3):  # pragma: no cover - defensive
        raise AssertionError("tracking prefill must not run for non-logistics intent")

    monkeypatch.setattr(ot, "fetch_tracking_prefill", _should_not_run)
    ctx = cal.get_dispatch_context(quickcep_session_id="prefill-2", env="LIVE")
    assert ctx["tracking"]["enabled"] is False
    assert ctx["tracking"]["reason"] == "intent_not_logistics_or_no_orders"
