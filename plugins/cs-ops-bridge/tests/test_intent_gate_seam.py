"""Seam tests for the CS_INTENT_ENABLED switch in intent_gate.py.

Verifies the two paths:
- switch OFF (default): legacy QuickCEP intentionTags logic, zero behavior change.
- switch ON + classifier reachable: delegates to classifier, gates on in_scope.
- switch ON + classifier unreachable: graceful fallback to legacy logic.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_intent_seam_test"


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
def _clean_env(monkeypatch):
    # Default: switch OFF
    monkeypatch.delenv("CS_INTENT_ENABLED", raising=False)
    monkeypatch.delenv("CS_INTENT_BASE_URL", raising=False)


def test_switch_off_legacy_allowed_tags(monkeypatch):
    ig = _load("intent_gate")
    # switch off → legacy: allowed tag passes
    res = ig.check_intent_gate(
        "s1",
        intention_tags=["产品咨询"],
        fetch_if_missing=False,
        env="TEST",
    )
    assert res.allowed is True
    assert res.reason == "allowed"


def test_switch_off_legacy_no_tags_blocks(monkeypatch):
    ig = _load("intent_gate")
    res = ig.check_intent_gate(
        "s2",
        intention_tags=None,
        fetch_if_missing=False,
        customer_email="new@x.com",
        env="TEST",
    )
    assert res.allowed is False
    assert res.reason == "no_intention_tags"


def test_switch_on_classifier_in_scope(monkeypatch):
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    fake_gate = {"in_scope": True, "primary_intent": "logistics_inquiry"}
    with patch.object(ig, "_classifier_gate", return_value=ig.IntentGateResult(True, "classifier:logistics_inquiry:in_scope", ())):
        res = ig.check_intent_gate("s3", intention_tags=["支付咨询"], fetch_if_missing=False, env="TEST")
    assert res.allowed is True
    assert res.reason.startswith("classifier:")


def test_switch_on_classifier_out_of_scope(monkeypatch):
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    # out_of_scope must return a reason starting with intention_not_allowed so
    # the watcher's permanent-skip check enqueues it into CAL (not transient).
    with patch.object(ig, "_classifier_gate", return_value=ig.IntentGateResult(False, "intention_not_allowed (classifier:after_sale_issue:out_of_scope)", ())):
        res = ig.check_intent_gate("s4", intention_tags=["产品咨询"], fetch_if_missing=False, env="TEST")
    assert res.allowed is False
    assert res.reason.startswith("intention_not_allowed")
    assert "out_of_scope" in res.reason


def test_switch_on_classifier_unreachable_falls_back(monkeypatch):
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    # _classifier_gate returns None (unreachable) → fall through to legacy
    with patch.object(ig, "_classifier_gate", return_value=None):
        res = ig.check_intent_gate("s5", intention_tags=["产品咨询"], fetch_if_missing=False, env="TEST")
    # legacy logic: 产品咨询 is allowed
    assert res.allowed is True
    assert res.reason == "allowed"


def test_switch_enabled_env_flag():
    ig = _load("intent_gate")
    import os

    os.environ["CS_INTENT_ENABLED"] = "true"
    try:
        assert ig._cs_intent_enabled() is True
    finally:
        del os.environ["CS_INTENT_ENABLED"]
    assert ig._cs_intent_enabled() is False


def test_extract_message_text_string_content():
    ig = _load("intent_gate")
    assert ig._extract_message_text({"content": "hello world"}) == "hello world"


def test_extract_message_text_dict_content_html():
    ig = _load("intent_gate")
    msg = {"contentType": "html", "content": {"content": "plain body text", "subject": "Re: order"}}
    assert ig._extract_message_text(msg) == "plain body text"


def test_extract_message_text_falls_back_to_body():
    ig = _load("intent_gate")
    assert ig._extract_message_text({"body": "fallback text"}) == "fallback text"
