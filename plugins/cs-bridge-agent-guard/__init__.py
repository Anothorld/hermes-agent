"""Hermes plugin — guard povison-cs runs from unsafe QuickCEP sends."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _load_hooks():
    hooks_path = Path(__file__).resolve().with_name("hooks.py")
    module_name = "hermes_plugins.cs_bridge_agent_guard.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hooks from {hooks_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.cs_bridge_agent_guard"
    spec.loader.exec_module(module)
    return module


def register(ctx) -> None:
    hooks = _load_hooks()
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("on_session_end", hooks.on_session_end)

    bridge_base = (os.environ.get("CS_OPS_BRIDGE_BASE") or "").strip()
    bridge_key = (
        os.environ.get("HERMES_CS_OPS_BRIDGE_KEY")
        or os.environ.get("CS_OPS_BRIDGE_KEY")
        or ""
    ).strip()
    if not bridge_base or not bridge_key:
        log.warning(
            "cs-bridge-agent-guard: CS_OPS_BRIDGE_BASE/HERMES_CS_OPS_BRIDGE_KEY not set — "
            "resume failure detection degraded to 4h timeout"
        )
