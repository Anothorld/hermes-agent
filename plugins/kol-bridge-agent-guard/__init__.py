"""Block bridge bypass patterns in agent tool calls (execute_code, curl, source reads)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_hooks():
    hooks_path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "hermes_plugins.kol_bridge_agent_guard.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {hooks_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.kol_bridge_agent_guard"
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    """Register Hermes lifecycle hooks."""
    hooks = _load_hooks()
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
