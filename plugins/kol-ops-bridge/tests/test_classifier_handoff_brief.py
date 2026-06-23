"""Tests for classifier handoff gateway brief."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INBOUND_ROOT = PLUGIN_ROOT / "inbound_reply"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_classifier_handoff_brief_mentions_templates():
    contract = _load_module(
        "bridge_agent_contract_handoff_test",
        PLUGIN_ROOT / "bridge_agent_contract.py",
    )
    block = contract.classifier_handoff_brief_block()
    assert "classifier-output.json" in block
    assert "sanitize-classifier-facts" in block
    assert "never open-escalation for JSON format" in block


def test_dispatcher_instructions_include_handoff_block():
    gateway = _load_module(
        "gateway_client_handoff_test",
        INBOUND_ROOT / "gateway_client.py",
    )
    text = gateway.dispatcher_instructions()
    assert "Classifier handoff" in text
    assert "classifier-handoff-checklist.md" in text


def test_legacy_dispatcher_instructions_include_handoff_block():
    legacy = _load_module(
        "kol_reply_dispatcher_legacy_handoff_test",
        PLUGIN_ROOT / "scripts" / "kol_reply_dispatcher_legacy.py",
    )
    text = legacy._dispatcher_instructions()
    assert "Classifier handoff" in text
    assert "classifier-handoff-checklist.md" in text
