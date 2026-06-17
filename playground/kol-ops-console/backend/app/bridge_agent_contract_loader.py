"""Load ``bridge_agent_contract`` from kol-ops-bridge (Console gateway briefs)."""

from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
from typing import Any

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "plugins"
    / "kol-ops-bridge"
    / "bridge_agent_contract.py"
)
# hermes-agent repo root — gateway terminal cwd is often $HOME, so briefs must
# embed absolute kol-bridge-cli paths (not relative plugins/...).
_REPO_ROOT = str(_CONTRACT_PATH.resolve().parents[2])


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
    return _contract_module().gateway_contract_block(repo_root=_REPO_ROOT)


def memory_layers_brief_block() -> str:
    return _contract_module().memory_layers_brief_block()


def format_hindsight_recall_seed(**kwargs: Any) -> str:
    return _contract_module().format_hindsight_recall_seed(**kwargs)


def gateway_contract_snippet() -> str:
    """Compact bridge rules for discovery briefs (saves ~1k tokens vs full block)."""
    cli = _contract_module().gateway_cli_invocation(_REPO_ROOT)
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
    kwargs.setdefault("repo_root", _REPO_ROOT)
    return _contract_module().resume_cli_checklist(**kwargs)


def draft_preview_cli_checklist(**kwargs: Any) -> str:
    kwargs.setdefault("repo_root", _REPO_ROOT)
    return _contract_module().draft_preview_cli_checklist(**kwargs)


def discovery_cli_rules() -> str:
    return _contract_module().discovery_cli_rules(repo_root=_REPO_ROOT)


def reply_dispatcher_cli_rules() -> str:
    return _contract_module().reply_dispatcher_cli_rules(repo_root=_REPO_ROOT)


def terminal_safety_rules(**kwargs: Any) -> str:
    kwargs.setdefault("repo_root", _REPO_ROOT)
    return _contract_module().terminal_safety_rules(**kwargs)


def approval_cli_checklist(**kwargs: Any) -> str:
    kwargs.setdefault("repo_root", _REPO_ROOT)
    return _contract_module().approval_cli_checklist(**kwargs)


def redraft_cli_checklist(**kwargs: Any) -> str:
    kwargs.setdefault("repo_root", _REPO_ROOT)
    return _contract_module().redraft_cli_checklist(**kwargs)


@lru_cache(maxsize=1)
def _dispatch_slimmer() -> Any:
    slim_path = (
        Path(__file__).resolve().parents[4]
        / "plugins"
        / "kol-ops-bridge"
        / "internal"
        / "dispatch_context_agent_view.py"
    )
    spec = importlib.util.spec_from_file_location(
        "kol_ops_bridge_dispatch_context_agent_view_console",
        slim_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load dispatch slimmer from {slim_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.slim_dispatch_context_for_agent


def slim_dispatch_context_for_agent(bundle: dict[str, Any]) -> dict[str, Any]:
    """Slim dispatch-context bundle for gateway brief embedding."""
    return _dispatch_slimmer()(bundle)
