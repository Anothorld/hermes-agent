"""Hermes pre_tool_call hook — reject incomplete Veedcrawl tool arguments early."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

HookResult = Optional[Union[None, Dict[str, str]]]

_VEEDCRAWL_PREFIX = "veedcrawl_"
_ARG_VALIDATE_MODULE = "hermes_plugins.veedcrawl._internal.arg_validate"


def _load_arg_validate():
    cached = sys.modules.get(_ARG_VALIDATE_MODULE)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("_internal") / "arg_validate.py"
    spec = importlib.util.spec_from_file_location(_ARG_VALIDATE_MODULE, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load arg_validate from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ARG_VALIDATE_MODULE] = module
    spec.loader.exec_module(module)
    return module


def pre_tool_call(
    tool_name: str,
    args: Dict[str, Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
) -> HookResult:
    del task_id, session_id, tool_call_id

    if not tool_name.startswith(_VEEDCRAWL_PREFIX):
        return None

    validate_tool_args = _load_arg_validate().validate_tool_args
    message = validate_tool_args(tool_name, args)
    if message:
        return {"action": "block", "message": message}
    return None
