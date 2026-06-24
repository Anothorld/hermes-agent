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


def _http_timeout() -> int:
    """HTTP read timeout (seconds) for the learning LLM call.

    Overridable via ``KOL_LEARNING_LLM_HTTP_TIMEOUT_SEC`` so operators can
    tune for a slow local proxy (e.g. Ollama/LiteLLM on 127.0.0.1:4000)
    without a code change. Defaults to 120s; clamped to [10, 600].
    """
    raw = os.environ.get("KOL_LEARNING_LLM_HTTP_TIMEOUT_SEC", "120").strip()
    try:
        return max(10, min(int(raw), 600))
    except ValueError:
        return 120


def _http_retries() -> int:
    """Extra retry attempts on *transient* LLM failures (timeout / 5xx / empty).

    Overridable via ``KOL_LEARNING_LLM_HTTP_RETRIES``. Default 1 (=> 2 total
    attempts). Clamped to [0, 3] so a flapping proxy can't stall the run.
    """
    raw = os.environ.get("KOL_LEARNING_LLM_HTTP_RETRIES", "1").strip()
    try:
        return max(0, min(int(raw), 3))
    except ValueError:
        return 1


class _TransientLlmError(RuntimeError):
    """Retryable LLM failure: read timeout, 5xx, or empty content."""


# #region agent log
def _debug_log(message: str, data: dict) -> None:
    """Best-effort NDJSON debug log (session 60e90d). Never raises."""
    try:
        import json as _json
        import time as _time

        line = _json.dumps({
            "sessionId": "60e90d",
            "hypothesisId": "LLM",
            "location": "learning_llm.py",
            "message": message,
            "data": data,
            "timestamp": int(_time.time() * 1000),
        }, ensure_ascii=False)
        with open("/Users/arnold/agent_prj/.cursor/debug-60e90d.log", "a", encoding="utf-8") as _fh:
            _fh.write(line + "\n")
    except Exception:
        pass
# #endregion


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


def _resolve_root_hermes_runtime() -> Optional[dict[str, str]]:
    try:
        from .internal.learning_hermes_runtime import resolve_root_openai_compatible_runtime

        return resolve_root_openai_compatible_runtime()
    except Exception as exc:
        logger.debug("Root Hermes runtime resolve failed: %s", exc)
        return None


def _runtime_signature(runtime: dict[str, str]) -> dict[str, str]:
    return {
        "base_url": str(runtime.get("base_url") or ""),
        "model": str(runtime.get("model") or ""),
        "source": str(runtime.get("source") or ""),
    }


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
                fallback = _resolve_root_hermes_runtime()
                if fallback and _runtime_signature(fallback) != _runtime_signature(runtime):
                    try:
                        return _invoke_openai_compatible(
                            prompt,
                            base_url=fallback["base_url"],
                            api_key=fallback["api_key"],
                            model=fallback["model"],
                        )
                    except Exception as fallback_exc:
                        raise LearningLlmError(
                            "Hermes-configured LLM HTTP call failed "
                            f"({runtime.get('base_url')} / {runtime.get('model')}): {exc}; "
                            f"root fallback ({fallback.get('base_url')} / "
                            f"{fallback.get('model')}) also failed: {fallback_exc}",
                        ) from fallback_exc
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
    timeout = _http_timeout()

    def _one_call() -> str:
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
            with _urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # 5xx are transient (proxy overload / upstream hiccup); 4xx are not.
            if 500 <= exc.code < 600:
                raise _TransientLlmError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            # Read timeout / connection reset against the proxy — retryable.
            raise _TransientLlmError(f"LLM network error: {exc}") from exc
        choices = payload.get("choices") or []
        if not choices:
            raise _TransientLlmError("LLM response missing choices")
        content = (choices[0].get("message") or {}).get("content") or ""
        if not str(content).strip():
            # Empty content from a flapping fallback model — retry once.
            raise _TransientLlmError("LLM response empty content")
        return str(content)

    attempts = _http_retries() + 1
    last_transient: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            result = _one_call()
            if attempt > 1:
                _debug_log("llm_recovered_after_retry", {
                    "base_url": base, "model": resolved_model, "attempt": attempt,
                })
            return result
        except _TransientLlmError as exc:
            last_transient = exc
            _debug_log("llm_transient_failure", {
                "base_url": base, "model": resolved_model,
                "attempt": attempt, "attempts": attempts,
                "timeout_sec": timeout, "error": str(exc)[:200],
            })
            if attempt >= attempts:
                break
    # Surface the transient as a plain RuntimeError so the existing
    # fallback chain in invoke_learning_llm() can try the root runtime.
    raise RuntimeError(str(last_transient) if last_transient else "LLM call failed")


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
    """Remove optional ``` fences and bare language tags from model output."""
    raw = text.strip()
    fence = re.search(r"```(?:json|markdown)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    # GLM and similar models sometimes emit `json` on its own line before `{...}`.
    if re.match(r"^json\s*$", raw, re.IGNORECASE):
        return ""
    if re.match(r"^json\s+[\[{]", raw, re.IGNORECASE):
        return raw.split(None, 1)[1].strip()
    if re.match(r"^json\s*\r?\n", raw, re.IGNORECASE):
        return raw.split("\n", 1)[1].strip()
    return raw
