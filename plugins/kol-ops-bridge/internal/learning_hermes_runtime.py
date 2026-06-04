"""Resolve Hermes agent model/credentials for learning distill (optional import)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_HERMES_AGENT_ROOT = Path(__file__).resolve().parents[3]
_PATH_PREPARED = False


def hermes_agent_available() -> bool:
    """True when hermes-agent package root is importable."""
    _prepare_hermes_agent_path()
    try:
        import hermes_cli.config  # noqa: F401
        return True
    except ImportError:
        return False


def _prepare_hermes_agent_path() -> None:
    global _PATH_PREPARED
    if _PATH_PREPARED:
        return
    root = str(_HERMES_AGENT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    _PATH_PREPARED = True


def _hermes_disabled() -> bool:
    return os.environ.get("KOL_LEARNING_LLM_DISABLE_HERMES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def apply_hermes_dotenv() -> None:
    """Hydrate process env from ``~/.hermes/.env`` (no overwrite)."""
    _prepare_hermes_agent_path()
    from hermes_cli.config import load_env

    for key, value in load_env().items():
        if value and not os.environ.get(key):
            os.environ[key] = value


def _main_model_name() -> str:
    from hermes_cli.config import load_config

    cfg = load_config()
    model_cfg = cfg.get("model")
    if isinstance(model_cfg, dict):
        return str(
            model_cfg.get("default") or model_cfg.get("model") or "",
        ).strip()
    if isinstance(model_cfg, str):
        return model_cfg.strip()
    return ""


def resolve_openai_compatible_runtime() -> Optional[dict[str, str]]:
    """Return base_url, api_key, model when Hermes runtime is OpenAI-compatible."""
    if not hermes_agent_available():
        return None
    apply_hermes_dotenv()
    from hermes_cli.runtime_provider import resolve_runtime_provider

    model = _main_model_name()
    runtime = resolve_runtime_provider(
        target_model=model or None,
    )
    api_mode = str(runtime.get("api_mode") or "chat_completions")
    if api_mode not in ("chat_completions", ""):
        return None
    api_key = str(runtime.get("api_key") or "").strip()
    if not api_key:
        return None
    base = str(runtime.get("base_url") or "").strip().rstrip("/")
    if not base:
        provider = str(runtime.get("provider") or "").strip().lower()
        if provider == "openrouter":
            from hermes_constants import OPENROUTER_BASE_URL

            base = OPENROUTER_BASE_URL.rstrip("/")
        else:
            base = "https://api.openai.com/v1"
    resolved_model = model or str(runtime.get("model") or "").strip()
    if not resolved_model:
        return None
    return {
        "base_url": base,
        "api_key": api_key,
        "model": resolved_model,
        "source": "hermes_config",
    }


def invoke_via_hermes_call_llm(
    prompt: str,
    *,
    system: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Use ``agent.auxiliary_client.call_llm`` (same stack as the main agent)."""
    if _hermes_disabled():
        raise RuntimeError("KOL_LEARNING_LLM_DISABLE_HERMES is set")
    if not hermes_agent_available():
        raise RuntimeError("hermes-agent package not importable from kol-ops-bridge")
    apply_hermes_dotenv()
    from agent.auxiliary_client import call_llm

    response = call_llm(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError("Hermes call_llm returned no choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not str(content or "").strip():
        raise RuntimeError("Hermes call_llm returned empty content")
    return str(content)
