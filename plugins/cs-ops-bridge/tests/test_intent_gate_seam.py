"""Seam tests for the CS_INTENT_ENABLED switch in intent_gate.py.

Verifies the two paths:
- switch OFF (default): legacy QuickCEP intentionTags logic, zero behavior change.
- switch ON + classifier reachable: delegates to classifier, gates on in_scope.
- switch ON + classifier unreachable: graceful fallback to legacy logic.
"""

from __future__ import annotations

import importlib.util
import json
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


# ── _latest_visitor_message: filter system messages before classifying ──

def _real_quickcep_messages() -> list:
    """Captured from LIVE get-messages for session 2551962190644895748
    (casalinilc@gmail.com, subject 'Table and chairs enquire').

    Order: chat_start (system) → customer email (visitor) → ruleAssignHumanQueue
    (system) → assignChat (system). The pre-fix code took messages[-1] = the
    assignChat system row and fed 'assignChat ... Support-8' to the LLM, which
    then misclassified the real customer email as spam_irrelevant.
    """
    return [
        {"id": "1", "content": '{"action":"chat_start"}', "contentType": "text",
         "ownerType": "system", "channel": "email"},
        {"id": "2", "contentType": "html",
         "content": {"from": "casalinilc@gmail.com", "to": "supportingcenter@povison.com",
                     "emailSubject": "Table and chairs enquire",
                     "content": "Just seeing when you will have these items in stock"},
         "ownerType": "visitor", "channel": "email"},
        {"id": "3", "content": '{"action":"ruleAssignHumanQueue","content":"自动分配Email"}',
         "contentType": "text", "ownerType": "system", "channel": "email"},
        {"id": "4", "content": '{"action":"assignChat","assignedOperator":"Charlotte","content":"Support-8"}',
         "contentType": "text", "ownerType": "system", "channel": "web"},
    ]


def test_latest_visitor_message_skips_system_rows():
    ig = _load("intent_gate")
    msgs = _real_quickcep_messages()
    picked = ig._latest_visitor_message(msgs)
    assert picked is not None
    assert picked["ownerType"] == "visitor"
    # The extracted body is the customer's actual text, not the assignChat notice.
    assert "in stock" in ig._extract_message_text(picked)


def test_latest_visitor_message_none_when_only_system():
    ig = _load("intent_gate")
    msgs = [
        {"content": '{"action":"chat_start"}', "ownerType": "system"},
        {"content": '{"action":"assignChat"}', "ownerType": "system"},
    ]
    assert ig._latest_visitor_message(msgs) is None


def test_latest_visitor_message_picks_newest_when_multiple_visits():
    ig = _load("intent_gate")
    msgs = [
        {"content": "first visitor msg", "ownerType": "visitor"},
        {"content": '{"action":"assignChat"}', "ownerType": "system"},
        {"content": "second visitor msg", "ownerType": "visitor"},
    ]
    picked = ig._latest_visitor_message(msgs)
    assert picked is not None
    assert ig._extract_message_text(picked) == "second visitor msg"


def test_seam_timeout_falls_back_to_legacy(monkeypatch):
    # When the seam work exceeds CS_INTENT_SEAM_TIMEOUT, the gate returns None
    # (→ legacy fallback) instead of blocking the watcher thread indefinitely.
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    monkeypatch.setenv("CS_INTENT_SEAM_TIMEOUT", "0.1")

    def slow_work(**kwargs):
        import time

        time.sleep(0.5)  # exceeds the 0.1s cap
        return ig.IntentGateResult(True, "classifier:logistics_inquiry:in_scope", ())

    monkeypatch.setattr(ig, "_classifier_gate_work", slow_work)
    # The seam submits to a real executor, so patch the work fn on the module
    # the executor calls. Since _classifier_gate references _classifier_gate_work
    # at call time via the module global, patching the module attribute works.
    res = ig.check_intent_gate("s-timeout", intention_tags=["产品咨询"], fetch_if_missing=False, env="TEST")
    # Timed out → None → fallthrough to legacy → 产品咨询 allowed
    assert res.allowed is True
    assert res.reason == "allowed"


def test_seam_timeout_env_default():
    ig = _load("intent_gate")
    assert ig._seam_timeout() == 45.0


# ── Idempotency cache: GET /gate-extract before POST /classify ──

def test_cache_hit_skips_post_classify(monkeypatch):
    """When the classifier already has a result, _classifier_gate_work reuses it
    and never calls POST /classify (no redundant LLM call).
    """
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")

    cached_ge = {"in_scope": False, "primary_intent": "spam_irrelevant"}
    post_calls: list = []

    def fake_cache_get(*, session_id, env):
        return cached_ge

    def fake_prefetch(*, session_id, env):
        post_calls.append("prefetch")
        return "body", []

    def fake_urlopen(*args, **kwargs):
        post_calls.append("POST")
        raise AssertionError("POST /classify must not be called on cache hit")

    monkeypatch.setattr(ig, "_fetch_cached_gate_extract", fake_cache_get)
    monkeypatch.setattr(ig, "_prefetch_body_and_orders", fake_prefetch)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    res = ig._classifier_gate_work(
        session_id="s-cache-hit", env="TEST", customer_email=None, info={}
    )
    assert res is not None
    assert res.allowed is False
    assert "intention_not_allowed" in res.reason
    assert "spam_irrelevant" in res.reason
    assert post_calls == []  # neither prefetch nor POST ran


def test_cache_miss_proceeds_to_post(monkeypatch):
    """When no cached result exists, _classifier_gate_work falls through to POST."""
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")

    def fake_cache_get(*, session_id, env):
        return None  # 404 / never classified

    monkeypatch.setattr(ig, "_fetch_cached_gate_extract", fake_cache_get)
    monkeypatch.setattr(ig, "_prefetch_body_and_orders", lambda *, session_id, env: ("body text", []))

    # Stub urlopen to return a successful classify response.
    import io

    class _FakeResp:
        def __init__(self, data):
            self._data = data
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    response_payload = json.dumps({"gate_extract": {"in_scope": True, "primary_intent": "product_inquiry"}}).encode()

    def fake_urlopen(req, timeout=None):
        assert timeout == ig._seam_timeout()  # urlopen timeout must match seam timeout
        return _FakeResp(response_payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    res = ig._classifier_gate_work(
        session_id="s-cache-miss", env="TEST", customer_email=None, info={}
    )
    assert res is not None
    assert res.allowed is True
    assert res.reason == "classifier:product_inquiry:in_scope"


def test_urlopen_timeout_matches_seam_timeout(monkeypatch):
    """Regression guard: urlopen timeout must NOT be a hardcoded 3s — it must
    follow _seam_timeout() so a 30s LLM call isn't aborted at 3s.
    """
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    monkeypatch.setenv("CS_INTENT_SEAM_TIMEOUT", "60")
    monkeypatch.setattr(ig, "_fetch_cached_gate_extract", lambda *, session_id, env: None)
    monkeypatch.setattr(ig, "_prefetch_body_and_orders", lambda *, session_id, env: ("body", []))

    captured = {}

    class _FakeResp:
        def read(self):
            return json.dumps({"gate_extract": {"in_scope": True, "primary_intent": "x"}}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ig._classifier_gate_work(session_id="s-t", env="TEST", customer_email=None, info={})
    assert captured["timeout"] == 60.0  # not 3.0


def test_cache_unreachable_still_proceeds_to_post(monkeypatch):
    """If the cache GET itself errors (classifier down), we still attempt POST
    (which will also fail → None → legacy fallback). No crash."""
    ig = _load("intent_gate")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")

    def fake_cache_get(*, session_id, env):
        raise ConnectionError("boom")

    monkeypatch.setattr(ig, "_fetch_cached_gate_extract", fake_cache_get)
    monkeypatch.setattr(ig, "_prefetch_body_and_orders", lambda *, session_id, env: ("body", []))

    def fake_urlopen(*args, **kwargs):
        raise ConnectionError("also down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    res = ig._classifier_gate_work(
        session_id="s-down", env="TEST", customer_email=None, info={}
    )
    assert res is None  # both cache + POST failed → graceful None → legacy fallback
