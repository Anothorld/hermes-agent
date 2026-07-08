"""Regression tests for the _wrap_errors task_id normalization.

The Hermes registry passes ``task_id`` inconsistently across call sites —
as a 2nd positional arg, as a keyword, or nested inside ``arguments``.
Before the fix this raised
``got multiple values for keyword argument 'task_id'`` on ``rpa_check_ip``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_tools_module():
    """Load the plugin's tools.py via importlib (bare ``import tools``
    would shadow the hermes-core ``tools`` package that tools.py imports
    ``tool_error``/``tool_result`` from)."""
    cached = sys.modules.get("kol_discovery_rpa_tools_wrap_errors_test")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "kol_discovery_rpa_tools_wrap_errors_test", _PLUGIN_ROOT / "tools.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["kol_discovery_rpa_tools_wrap_errors_test"] = module
    return module


rpa_tools = _load_tools_module()


def _make_handler():
    captured: dict = {}

    def handler(args, *, task_id="", **_):
        captured["args"] = args
        captured["task_id"] = task_id
        return '{"ok": true}'

    return handler, captured


def test_wrapper_task_id_as_keyword():
    handler, captured = _make_handler()
    wrapped = rpa_tools._wrap_errors(handler)
    wrapped({"handle": "x"}, task_id="run-123")
    assert captured["task_id"] == "run-123"
    assert captured["args"] == {"handle": "x"}


def test_wrapper_task_id_as_second_positional():
    """Registry sometimes passes task_id as the 2nd positional argument."""
    handler, captured = _make_handler()
    wrapped = rpa_tools._wrap_errors(handler)
    wrapped({"handle": "x"}, "run-456")
    assert captured["task_id"] == "run-456"


def test_wrapper_task_id_nested_in_arguments():
    """Some call sites merge task_id into the arguments dict itself."""
    handler, captured = _make_handler()
    wrapped = rpa_tools._wrap_errors(handler)
    wrapped({"handle": "x", "task_id": "run-789"})
    assert captured["task_id"] == "run-789"
    # task_id should be pulled OUT of args so handlers don't see it twice
    assert "task_id" not in captured["args"]


def test_wrapper_task_id_absent_uses_default():
    handler, captured = _make_handler()
    wrapped = rpa_tools._wrap_errors(handler)
    wrapped({"handle": "x"})
    assert captured["task_id"] == ""


def test_wrapper_keyword_task_id_wins_over_nested():
    handler, captured = _make_handler()
    wrapped = rpa_tools._wrap_errors(handler)
    wrapped({"handle": "x", "task_id": "nested"}, task_id="explicit")
    assert captured["task_id"] == "explicit"
    assert "task_id" not in captured["args"]
