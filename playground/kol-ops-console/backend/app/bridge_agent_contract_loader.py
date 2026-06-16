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


def gateway_contract_snippet() -> str:
    """Compact bridge rules for discovery briefs (saves ~1k tokens vs full block)."""
    cli = _contract_module().CLI_INVOCATION
    return (
        "Bridge CAL (mandatory): "
        f"{cli} <subcommand> --env LIVE|TEST. "
        "Dispatch reads: get-dispatch-context --view agent. "
        "Forbidden: execute_code, curl/urllib/requests to :8080, "
        "direct cal.py/DB, reading plugins/kol-ops-bridge/ for API discovery. "
        "Full contract: kol-bridge-agent-guard skill + bridge_agent_contract."
    )


def gateway_contract_for_brief(*, compact: bool | None = None) -> str:
    """Pick compact vs full contract based on ``KOC_BRIEF_COMPACT_CONTRACT``."""
    from .config import get_settings

    use_compact = (
        get_settings().brief_compact_contract if compact is None else compact
    )
    return gateway_contract_snippet() if use_compact else gateway_contract_block()


def resume_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().resume_cli_checklist(**kwargs)


def draft_preview_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().draft_preview_cli_checklist(**kwargs)


def discovery_cli_rules() -> str:
    return _contract_module().discovery_cli_rules()


def reply_dispatcher_cli_rules() -> str:
    return _contract_module().reply_dispatcher_cli_rules()


def terminal_safety_rules(**kwargs: Any) -> str:
    return _contract_module().terminal_safety_rules(**kwargs)


def approval_cli_checklist(**kwargs: Any) -> str:
    return _contract_module().approval_cli_checklist(**kwargs)
