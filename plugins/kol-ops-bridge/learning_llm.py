"""LLM helpers for learning distill (Hermes runtime + stdlib HTTP)."""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "You write concise markdown for operators to review."


class LearningLlmError(RuntimeError):
    """Learning distill requires a successful LLM call (no silent rule fallback)."""


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


def _openai_sdk_available() -> bool:
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_hermes_runtime() -> Optional[dict[str, str]]:
    try:
        from .internal.learning_hermes_runtime import resolve_openai_compatible_runtime

        return resolve_openai_compatible_runtime()
    except Exception as exc:
        logger.debug("Hermes runtime resolve failed: %s", exc)
        return None


def invoke_learning_llm(
    prompt: str,
    *,
    runner: Optional[Callable[[str], str]] = None,
) -> str:
    """Return raw model text; raises :class:`LearningLlmError` on failure.

    Resolution order:
    1. ``runner`` (tests)
    2. ``KOL_LEARNING_LLM_CMD`` shell command
    3. Explicit ``KOL_LEARNING_LLM_*`` env (override)
    4. Hermes-resolved OpenAI-compatible HTTP (``~/.hermes`` config)
    5. Hermes ``call_llm`` when ``openai`` package is installed
    6. Legacy env: ``OPENAI_API_KEY`` / classifier eval vars
    """
    if runner is not None:
        return runner(prompt)
    cmd = os.environ.get("KOL_LEARNING_LLM_CMD", "").strip()
    if cmd:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("KOL_LEARNING_LLM_CMD_TIMEOUT", "180")),
                check=False,
            )
        except Exception as exc:
            raise LearningLlmError(f"KOL_LEARNING_LLM_CMD failed: {exc}") from exc
        if proc.returncode != 0:
            raise LearningLlmError(
                f"KOL_LEARNING_LLM_CMD exit {proc.returncode}: {proc.stderr[:500]}",
            )
        if not proc.stdout.strip():
            raise LearningLlmError("KOL_LEARNING_LLM_CMD returned empty output")
        return proc.stdout
    if _has_explicit_learning_override():
        return _invoke_openai_compatible(prompt)
    if not _hermes_disabled():
        runtime = _resolve_hermes_runtime()
        if runtime:
            try:
                return _invoke_openai_compatible(
                    prompt,
                    base_url=runtime["base_url"],
                    api_key=runtime["api_key"],
                    model=runtime["model"],
                )
            except Exception as exc:
                raise LearningLlmError(
                    "Hermes-configured LLM HTTP call failed "
                    f"({runtime.get('base_url')} / {runtime.get('model')}): {exc}",
                ) from exc
        if _openai_sdk_available():
            try:
                from .internal.learning_hermes_runtime import invoke_via_hermes_call_llm

                return invoke_via_hermes_call_llm(
                    prompt,
                    system=_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=_max_tokens(),
                )
            except Exception as exc:
                raise LearningLlmError(f"Hermes call_llm failed: {exc}") from exc
        raise LearningLlmError(
            "No usable Hermes LLM runtime. Ensure ~/.hermes/config.yaml has a "
            "chat-completions provider + API key, or set KOL_LEARNING_LLM_API_KEY. "
            "If using call_llm, install the openai package in the bridge venv.",
        )
    try:
        return _invoke_openai_compatible(prompt)
    except Exception as exc:
        raise LearningLlmError(
            "No LLM credentials for learning distill. Configure Hermes "
            "(~/.hermes/config.yaml + .env) or set KOL_LEARNING_LLM_API_KEY / "
            "OPENAI_API_KEY, or KOL_LEARNING_LLM_CMD.",
        ) from exc


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


def _urlopen(req: urllib.request.Request, *, timeout: int) -> object:
    """HTTPS with certifi when available (macOS Python often lacks system CAs)."""
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except ImportError:
        return urllib.request.urlopen(req, timeout=timeout)


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
        "max_tokens": _max_tokens(),
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
        with _urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM network error: {exc}") from exc
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
