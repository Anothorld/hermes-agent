"""Tests for the gate_extract brief seam in bridge_agent_contract.py.

Verifies:
- CS_INTENT_ENABLED=false → no # gate_extract block (legacy brief unchanged).
- CS_INTENT_ENABLED=true + classifier returns gate_extract → block present with constraints.
- CS_INTENT_ENABLED=true + classifier unreachable → block absent (graceful).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_brief_seam_test"


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
    monkeypatch.delenv("CS_INTENT_ENABLED", raising=False)
    monkeypatch.delenv("CS_INTENT_BASE_URL", raising=False)


def test_switch_off_no_gate_block(monkeypatch):
    bac = _load("bridge_agent_contract")
    brief = bac.process_cli_checklist(env="LIVE", quickcep_session_id="s1")
    assert "# gate_extract" not in brief
    # legacy checklist still present
    assert "# bridge_cli_checklist" in brief
    assert "classify-intent" in brief  # step 3 preserved


def test_switch_on_classifier_returns_block(monkeypatch):
    bac = _load("bridge_agent_contract")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    fake_ge = {
        "intents": [{"intent": "logistics_inquiry", "in_scope": True, "confidence": "high", "urgency": "medium", "related_orders": ["12345"], "snippet": "Where is my order?"}],
        "primary_intent": "logistics_inquiry",
        "in_scope": True,
        "route": "auto_handle",
        "urgency": "medium",
        "emotion": {"value": "neutral", "confidence": "medium"},
        "language": {"value": "en", "confidence": 0.99},
        "products": [],
        "orders": ["12345"],
        "customer_region": {"country": "US", "province_state": "CA", "source": "order_address", "confidence": "high"},
        "customer_segment": "returning",
        "summary_zh": "客户问物流",
        "hindsight_keywords": ["tracking"],
        "conversation_stage": "follow_up",
        "response_template_hint": "logistics_tracking",
        "uncertain_fields": [],
        "null_fields": [],
        "fabrication_guard": True,
        "model_version": "v1",
        "classifier_source": "llm",
        "pii_flag": False,
        "threat_signal": None,
        "ambiguous": False,
    }
    with patch.object(bac, "_fetch_gate_extract", return_value=fake_ge):
        brief = bac.process_cli_checklist(env="LIVE", quickcep_session_id="s1")
    assert "# gate_extract" in brief
    assert "logistics_inquiry" in brief
    assert "Do NOT re-run classify-intent" in brief
    assert "fabrication guard" in brief.lower() or "Fabrication guard" in brief
    # legacy checklist still present after the gate block
    assert "# bridge_cli_checklist" in brief


def test_switch_on_classifier_unreachable_no_block(monkeypatch):
    bac = _load("bridge_agent_contract")
    monkeypatch.setenv("CS_INTENT_ENABLED", "true")
    with patch.object(bac, "_fetch_gate_extract", return_value=None):
        brief = bac.process_cli_checklist(env="LIVE", quickcep_session_id="s1")
    # classifier unreachable → no gate block → legacy brief unchanged
    assert "# gate_extract" not in brief
    assert "# bridge_cli_checklist" in brief


def test_render_gate_extract_brief_contains_confirmation_rule(monkeypatch):
    bac = _load("bridge_agent_contract")
    ge = {
        "intents": [{"intent": "after_sale_issue", "in_scope": False, "confidence": "high", "urgency": "high", "related_products": [{"slug": "SF8268"}], "snippet": "sofa damaged"}],
        "primary_intent": "after_sale_issue",
        "in_scope": False,
        "route": "escalate",
        "urgency": "high",
        "emotion": {"value": "angry", "confidence": "high"},
        "language": {"value": "en", "confidence": 0.99},
        "products": [{"slug": "SF8268"}],
        "orders": [],
        "customer_region": {"country": None, "province_state": None, "source": "unknown", "confidence": "low"},
        "customer_segment": "returning",
        "summary_zh": "沙发破损",
        "hindsight_keywords": ["SF8268", "damaged"],
        "conversation_stage": "follow_up",
        "response_template_hint": "after_sale_escalate",
        "uncertain_fields": ["intents[0].related_products", "customer_region"],
        "null_fields": ["customer_region"],
        "fabrication_guard": True,
        "model_version": "v1",
        "classifier_source": "llm",
        "pii_flag": False,
        "threat_signal": None,
        "ambiguous": False,
    }
    block = bac._render_gate_extract_brief(ge)
    assert "求证规则" in block
    assert "uncertain_fields" in block.lower() or "Uncertain fields" in block
    assert "null_fields" in block.lower() or "Null fields" in block
