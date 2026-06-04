"""Load ``bridge_agent_contract`` from kol-ops-bridge (Console gateway briefs)."""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "plugins"
    / "kol-ops-bridge"
    / "bridge_agent_contract.py"
)


@lru_cache(maxsize=1)
def _contract_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_bridge_agent_contract",
        _CONTRACT_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load bridge contract from {_CONTRACT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gateway_contract_block() -> str:
    return _contract_module().gateway_contract_block()


def resume_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().resume_cli_checklist(**kwargs)


def draft_preview_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().draft_preview_cli_checklist(**kwargs)


def discovery_cli_rules() -> str:
    return _contract_module().discovery_cli_rules()


def reply_dispatcher_cli_rules() -> str:
    return _contract_module().reply_dispatcher_cli_rules()


def terminal_safety_rules() -> str:
    return _contract_module().terminal_safety_rules()


def approval_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().approval_cli_checklist(**kwargs)
