"""Optional LLM helpers for learning distill (stdlib HTTP + optional Hermes agent)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "You write concise markdown for operators to review."


def _has_explicit_learning_override() -> bool:
    """Dedicated learning env vars override Hermes agent defaults."""
    return bool(
        os.environ.get("KOL_LEARNING_LLM_API_KEY", "").strip()
        or os.environ.get("KOL_LEARNING_LLM_MODEL", "").strip()
        or os.environ.get("KOL_LEARNING_LLM_API_URL", "").strip()
    )


def _max_tokens() -> int:
    raw = os.environ.get("KOL_LEARNING_LLM_MAX_TOKENS", "4096").strip()
    try:
        return max(256, min(int(raw), 32_000))
    except ValueError:
        return 4096


def invoke_learning_llm(
    prompt: str,
    *,
    runner: Optional[Callable[[str], str]] = None,
) -> str:
    """Return raw model text.

    Resolution order:
    1. ``runner`` (tests)
    2. ``KOL_LEARNING_LLM_CMD`` shell command
    3. Explicit ``KOL_LEARNING_LLM_*`` env (override)
    4. Hermes agent — ``call_llm`` with the same model/credentials as CLI
    5. Hermes-resolved OpenAI-compatible HTTP (stdlib)
    6. Legacy env: ``OPENAI_API_KEY`` / classifier eval vars
    """
    if runner is not None:
        return runner(prompt)
    cmd = os.environ.get("KOL_LEARNING_LLM_CMD", "").strip()
    if cmd:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("KOL_LEARNING_LLM_CMD_TIMEOUT", "180")),
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"KOL_LEARNING_LLM_CMD exit {proc.returncode}: {proc.stderr[:500]}",
            )
        return proc.stdout
    if _has_explicit_learning_override():
        return _invoke_openai_compatible(prompt)
    if not _hermes_disabled():
        try:
            from .internal.learning_hermes_runtime import invoke_via_hermes_call_llm

            return invoke_via_hermes_call_llm(
                prompt,
                system=_SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=_max_tokens(),
            )
        except Exception as exc:
            logger.warning(
                "Hermes agent call_llm unavailable for learning distill; "
                "trying OpenAI-compatible fallback (%s)",
                exc,
            )
        try:
            from .internal.learning_hermes_runtime import resolve_openai_compatible_runtime

            runtime = resolve_openai_compatible_runtime()
            if runtime:
                return _invoke_openai_compatible(
                    prompt,
                    base_url=runtime["base_url"],
                    api_key=runtime["api_key"],
                    model=runtime["model"],
                )
        except Exception as exc:
            logger.debug("Hermes OpenAI-compatible resolve failed: %s", exc)
    return _invoke_openai_compatible(prompt)


def _hermes_disabled() -> bool:
    return os.environ.get("KOL_LEARNING_LLM_DISABLE_HERMES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _env_lookup(key: str) -> Optional[str]:
    val = os.environ.get(key)
    if val:
        return val
    try:
        from .internal.learning_hermes_runtime import apply_hermes_dotenv, hermes_agent_available

        if hermes_agent_available():
            apply_hermes_dotenv()
            from hermes_cli.config import get_env_value

            return get_env_value(key)
    except Exception:
        pass
    return None


def _invoke_openai_compatible(
    prompt: str,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    base = (
        base_url
        or os.environ.get("KOL_LEARNING_LLM_API_URL")
        or os.environ.get("KOL_CLASSIFIER_EVAL_API_URL")
        or _env_lookup("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    key = (
        api_key
        or os.environ.get("KOL_LEARNING_LLM_API_KEY")
        or os.environ.get("KOL_CLASSIFIER_EVAL_API_KEY")
        or _env_lookup("OPENAI_API_KEY")
        or _env_lookup("OPENROUTER_API_KEY")
    )
    if not key:
        raise RuntimeError(
            "No LLM credentials for style distill. Configure Hermes agent "
            "(~/.hermes/config.yaml + .env with a provider API key), or set "
            "KOL_LEARNING_LLM_API_KEY / OPENAI_API_KEY, or KOL_LEARNING_LLM_CMD.",
        )
    resolved_model = (
        model
        or os.environ.get("KOL_LEARNING_LLM_MODEL")
        or os.environ.get("KOL_CLASSIFIER_EVAL_MODEL")
        or _main_model_from_hermes()
        or "gpt-4.1-mini"
    )
    body = json.dumps({
        "model": resolved_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response missing choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise RuntimeError("LLM response empty content")
    return str(content)


def _main_model_from_hermes() -> Optional[str]:
    try:
        from .internal.learning_hermes_runtime import _main_model_name, hermes_agent_available

        if hermes_agent_available():
            name = _main_model_name()
            return name or None
    except Exception:
        pass
    return None


def strip_markdown_fences(text: str) -> str:
    """Remove optional ``` fences from model output."""
    raw = text.strip()
    fence = re.search(r"```(?:markdown)?\s*([\s\S]*?)```", raw)
    if fence:
        return fence.group(1).strip()
    return raw
