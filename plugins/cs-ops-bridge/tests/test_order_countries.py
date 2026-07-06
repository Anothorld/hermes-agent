"""Tests for order_tracking.fetch_order_countries (cs-intent-classifier region source)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_order_country_test"


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


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    # reset the module-level circuit breaker between tests
    ot = _load("order_tracking")
    ot._cb_consecutive_failures = 0
    ot._cb_open_until = 0.0
    yield
    ot._cb_consecutive_failures = 0
    ot._cb_open_until = 0.0


def test_fetch_order_countries_extracts_country():
    ot = _load("order_tracking")
    fake_payload = {"info": {"records": [{"country": "AU", "state": "NSW"}]}}
    with patch.object(ot, "_fetch_one", return_value=(fake_payload, None)), \
         patch.object(ot, "_fetch_billing_state", return_value="Wisconsin"):
        out = ot.fetch_order_countries(["O1"])
    assert out == [{"order_id": "O1", "country": "AU", "province_state": "Wisconsin"}]


def test_fetch_order_countries_province_state_none_when_billing_unavailable():
    ot = _load("order_tracking")
    fake_payload = {"info": {"records": [{"country": "US"}]}}
    with patch.object(ot, "_fetch_one", return_value=(fake_payload, None)), \
         patch.object(ot, "_fetch_billing_state", return_value=None):
        out = ot.fetch_order_countries(["O2"])
    assert out == [{"order_id": "O2", "country": "US", "province_state": None}]


def test_fetch_billing_state_parses_comma_address():
    ot = _load("order_tracking")
    # billingAddress "street,city,state,country" → state = parts[-2]
    fake_qc = type("M", (), {"get_jwt": lambda *a: "jwt", "api_request": lambda *a, **k: {"data": {"billingAddress": "990 Holly Ridge Ct,Brookfield,Wisconsin,US"}}})()
    import sys
    sys.modules["quickcep_cli"] = fake_qc  # type: ignore[assignment]
    try:
        state = ot._fetch_billing_state("O3")
    finally:
        del sys.modules["quickcep_cli"]
    assert state == "Wisconsin"


def test_fetch_billing_state_returns_none_on_short_address():
    ot = _load("order_tracking")
    fake_qc = type("M", (), {"get_jwt": lambda *a: "jwt", "api_request": lambda *a, **k: {"data": {"billingAddress": "US"}}})()
    import sys
    sys.modules["quickcep_cli"] = fake_qc  # type: ignore[assignment]
    try:
        state = ot._fetch_billing_state("O4")
    finally:
        del sys.modules["quickcep_cli"]
    assert state is None


def test_fetch_billing_state_returns_none_on_missing_field():
    ot = _load("order_tracking")
    fake_qc = type("M", (), {"get_jwt": lambda *a: "jwt", "api_request": lambda *a, **k: {"data": {}}})()
    import sys
    sys.modules["quickcep_cli"] = fake_qc  # type: ignore[assignment]
    try:
        state = ot._fetch_billing_state("O5")
    finally:
        del sys.modules["quickcep_cli"]
    assert state is None


def test_fetch_order_countries_empty_input():
    ot = _load("order_tracking")
    assert ot.fetch_order_countries([]) == []


def test_fetch_order_countries_api_failure_returns_none_country():
    ot = _load("order_tracking")
    with patch.object(ot, "_fetch_one", return_value=(None, "timeout")), \
         patch.object(ot, "_fetch_billing_state", return_value=None):
        out = ot.fetch_order_countries(["O4"])
    assert out == [{"order_id": "O4", "country": None, "province_state": None}]


def test_fetch_order_countries_dedupes_and_caps():
    ot = _load("order_tracking")
    fake = {"info": {"records": [{"country": "US"}]}}
    with patch.object(ot, "_fetch_one", return_value=(fake, None)) as mock_fetch, \
         patch.object(ot, "_fetch_billing_state", return_value=None):
        ot.fetch_order_countries(["O5", "O5", "O6", "O7", "O8"], max_orders=2)
    # dedupe: O5 once, O6 once → 2 calls (O7/O8 capped out)
    assert mock_fetch.call_count == 2
