"""Tests for learning_llm JSON fence stripping."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _learning_llm():
    path = Path(__file__).resolve().parents[1] / "learning_llm.py"
    spec = importlib.util.spec_from_file_location("learning_llm_fence_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_strip_markdown_json_fence():
    llm = _learning_llm()
    raw = '```json\n{"SEB8008": {"category": "sofa"}}\n```'
    assert llm.strip_markdown_fences(raw) == '{"SEB8008": {"category": "sofa"}}'


def test_strip_bare_json_language_line():
    llm = _learning_llm()
    raw = 'json\n{\n  "POVISON-TS-8319": {"category": "tv_stand"}\n}'
    assert '"POVISON-TS-8319"' in llm.strip_markdown_fences(raw)
