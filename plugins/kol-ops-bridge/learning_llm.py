"""Optional LLM helpers for learning distill (stdlib HTTP only)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional


def invoke_learning_llm(
    prompt: str,
    *,
    runner: Optional[Callable[[str], str]] = None,
) -> str:
    """Return raw model text. Uses runner, CMD, or OpenAI-compatible API."""
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
    return _invoke_openai_compatible(prompt)


def _invoke_openai_compatible(prompt: str) -> str:
    base = (
        os.environ.get("KOL_LEARNING_LLM_API_URL")
        or os.environ.get("KOL_CLASSIFIER_EVAL_API_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    api_key = (
        os.environ.get("KOL_LEARNING_LLM_API_KEY")
        or os.environ.get("KOL_CLASSIFIER_EVAL_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "set KOL_LEARNING_LLM_API_KEY or OPENAI_API_KEY for LLM style distill",
        )
    model = os.environ.get(
        "KOL_LEARNING_LLM_MODEL",
        os.environ.get("KOL_CLASSIFIER_EVAL_MODEL", "gpt-4.1-mini"),
    )
    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You write concise markdown for operators to review."},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
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


def strip_markdown_fences(text: str) -> str:
    """Remove optional ``` fences from model output."""
    raw = text.strip()
    fence = re.search(r"```(?:markdown)?\s*([\s\S]*?)```", raw)
    if fence:
        return fence.group(1).strip()
    return raw
