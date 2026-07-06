"""Learning loop for cs-intent-classifier.

T1 error bank + T2 few-shot injection + T3 offline policy distillation.

- T1: golden + failure cases in eval/cases/*.jsonl; eval_runner scores them.
- T2: build_few_shot_block() — recent high-confidence corrections injected into
  the classifier prompt as few-shot examples (online, no approve).
- T3: distill_intent_policy_llm() — cron aggregates corrections and asks an LLM
  to generate/update config/intent_policy.md rules. Self-adaptive:
  latest_weekly_trend() compares this week vs last week pass-rate:
    - this_week >= last_week → mode="repair" (incremental ADJUST:/REMOVE:)
    - this_week <  last_week → mode="rebuild" (regenerate from cumulative samples)
  Candidate policy must pass eval (>= current accuracy) to auto-promote.
  No human approve step. All decisions audited in cs_learning_job_runs.

No-fabrication invariant: distill failure falls back to deterministic aggregate;
empty markdown never promotes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from . import db

log = logging.getLogger(__name__)
_CONFIG_DIR = Path(__file__).resolve().parent / "config"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_learning_config() -> dict[str, Any]:
    path = _CONFIG_DIR / "intent_learning.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        data = {}
    # env overrides
    for key in ("distill_period", "eval_period", "promote_min_accuracy_delta", "rebuild_threshold", "fewshot_period", "fewshot_sample_size"):
        env_key = f"CS_INTENT_{key.upper()}"
        if env_key in os.environ:
            raw = os.environ[env_key]
            try:
                if key in ("promote_min_accuracy_delta", "rebuild_threshold"):
                    data[key] = float(raw)
                else:
                    data[key] = raw
            except ValueError:
                pass
    return data


# ── T1: corrections listing ──


def list_intent_corrections(*, env: str, since: str = "", until: str = "", limit: int = 200) -> list[dict[str, Any]]:
    return db.list_corrections(env=env, since=since, until=until, limit=limit)


# ── T2: few-shot block ──


def build_few_shot_block(*, env: str, n: int = 0) -> str:
    """Render recent high-confidence corrections as a few-shot prompt block.

    Injected into the classifier LLM prompt at call time. Returns "" when no
    corrections are available.
    """
    if n <= 0:
        n = int(_load_learning_config().get("fewshot_sample_size", 8))
    corrections = db.list_corrections(env=env, limit=n * 3)
    # keep only corrected != predicted primary_intent, high-signal
    examples: list[dict[str, Any]] = []
    for c in corrections:
        pred = c.get("predicted") or {}
        corr = c.get("corrected") or {}
        if (pred.get("primary_intent") or "") == (corr.get("primary_intent") or ""):
            continue
        examples.append({
            "subject": "",  # not stored in correction row; would need enrichment
            "predicted": pred.get("primary_intent"),
            "corrected": corr.get("primary_intent"),
            "reason": c.get("reason") or "",
        })
        if len(examples) >= n:
            break
    if not examples:
        return ""
    lines = ["## Few-shot corrections (recent operator overrides — learn from these)"]
    for ex in examples:
        lines.append(f"- predicted={ex['predicted']} → corrected={ex['corrected']} ({ex['reason']})")
    return "\n".join(lines) + "\n"


# ── T3: offline policy distillation ──


def build_policy_rules_block() -> str:
    """Inject config/intent_policy.md rules into the classifier prompt."""
    path = _CONFIG_DIR / "intent_policy.md"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    return f"## Distilled policy rules (auto-generated, v from intent_policy.md)\n{content}\n"


def latest_weekly_trend(*, env: str, weeks: int = 2) -> dict[str, Any]:
    """Compare this week vs last week average accuracy from cs_intent_eval_daily.

    Returns {this_week, last_week, delta, direction: "up"|"down"|"flat"|"insufficient"}.
    """
    snapshots = db.eval_trend(env=env, days=weeks * 7 + 1)
    if not snapshots:
        return {"this_week": None, "last_week": None, "delta": None, "direction": "insufficient"}
    now = _utcnow()
    this_week_start = (now - timedelta(days=7)).date()
    last_week_start = (now - timedelta(days=14)).date()
    this_week_vals = [s["accuracy"] for s in snapshots if _parse_date(s["date"]) >= this_week_start]
    last_week_vals = [
        s["accuracy"]
        for s in snapshots
        if last_week_start <= _parse_date(s["date"]) < this_week_start
    ]
    if not this_week_vals or not last_week_vals:
        return {"this_week": None, "last_week": None, "delta": None, "direction": "insufficient"}
    tw = sum(this_week_vals) / len(this_week_vals)
    lw = sum(last_week_vals) / len(last_week_vals)
    delta = tw - lw
    direction = "up" if delta >= 0 else "down"
    return {"this_week": tw, "last_week": lw, "delta": delta, "direction": direction}


def _parse_date(s: str) -> Any:
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None


def distill_intent_policy_llm(
    *,
    env: str,
    baseline_md: str,
    samples: list[dict[str, Any]],
    mode: str = "repair",
) -> tuple[str, bool]:
    """T3 offline distillation. Returns (markdown, used_llm).

    mode="repair": incremental ADJUST:/REMOVE: edits on baseline.
    mode="rebuild": regenerate from cumulative samples, ignore baseline.
    Falls back to deterministic aggregate on LLM failure.
    """
    if mode not in ("repair", "rebuild"):
        mode = "repair"
    llm_cfg = _llm_config()
    if not llm_cfg["api_key"] or not llm_cfg["model"]:
        log.warning("distill: LLM not configured — deterministic aggregate fallback")
        return _deterministic_aggregate(samples), False
    prompt = _build_distill_prompt(baseline_md=baseline_md, samples=samples, mode=mode)
    raw = _call_llm(llm_cfg, prompt, timeout=30.0)
    if not raw:
        log.warning("distill: LLM call failed — deterministic aggregate fallback")
        return _deterministic_aggregate(samples), False
    md = _strip_fences(raw).strip()
    if not md:
        log.warning("distill: empty markdown — deterministic aggregate fallback")
        return _deterministic_aggregate(samples), False
    return md, True


def _build_distill_prompt(*, baseline_md: str, samples: list[dict[str, Any]], mode: str) -> str:
    samples_block = json.dumps(
        [
            {
                "predicted": (c.get("predicted") or {}).get("primary_intent"),
                "corrected": (c.get("corrected") or {}).get("primary_intent"),
                "reason": c.get("reason") or "",
            }
            for c in samples
        ],
        ensure_ascii=False,
        indent=2,
    )
    if mode == "rebuild":
        baseline_section = "Rebuild mode: ignore any prior policy. Generate fresh rules from the samples."
    else:
        baseline_section = f"Repair mode: edit the baseline with ADJUST:/REMOVE: prefixes.\n\nCURRENT baseline:\n{baseline_md or '(empty)'}"
    return (
        "You analyze operator intent corrections to improve an email intent classifier.\n"
        "Produce markdown rules for the classifier. Use ADJUST: to revise or add a rule, REMOVE: to delete one.\n"
        "Cite evidence (predicted→corrected pairs). Do not invent rules not supported by the samples.\n"
        f"{baseline_section}\n\n"
        "Output ONLY markdown starting with `## Approved intent policy`. 3-8 bullets.\n\n"
        f"SAMPLES_JSON:\n{samples_block}"
    )


def _deterministic_aggregate(samples: list[dict[str, Any]]) -> str:
    """Fallback: tally the most common predicted→corrected transitions as rules."""
    counts: dict[tuple[str, str], int] = {}
    for c in samples:
        pred = (c.get("predicted") or {}).get("primary_intent") or "?"
        corr = (c.get("corrected") or {}).get("primary_intent") or "?"
        counts[(pred, corr)] = counts.get((pred, corr), 0) + 1
    lines = ["## Approved intent policy (deterministic fallback)"]
    for (pred, corr), n in sorted(counts.items(), key=lambda x: -x[1])[:8]:
        lines.append(f"- ADJUST: when predicted={pred} but email signals {corr}, prefer {corr} (n={n})")
    return "\n".join(lines) + "\n"


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text


def _llm_config() -> dict[str, str]:
    return {
        "provider": os.environ.get("CS_INTENT_LLM_PROVIDER", "").strip(),
        "model": os.environ.get("CS_INTENT_LLM_MODEL", "").strip(),
        "api_key": os.environ.get("CS_INTENT_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")).strip(),
        "base_url": os.environ.get("CS_INTENT_LLM_BASE_URL", "").strip(),
    }


def _call_llm(cfg: dict[str, str], prompt: str, timeout: float) -> Optional[str]:
    base = cfg["base_url"] or "https://api.openai.com/v1"
    url = base.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 1000},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {cfg['api_key']}"}
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("choices") or [{}])[0].get("message", {}).get("content")
    except Exception as exc:
        log.error("distill LLM call failed: %s", exc)
        return None


# ── Promote (auto, no approve) ──


def promote_intent_prompt(*, new_policy_md: str, eval_snapshot: dict[str, Any]) -> bool:
    """Write the new policy to config/intent_policy.md and bump version. Auto, no approve.

    Caller must have already verified eval passes the gate. Returns True on success.
    """
    policy_path = _CONFIG_DIR / "intent_policy.md"
    version_path = _CONFIG_DIR / "intent_version.txt"
    # archive old
    archive_dir = _CONFIG_DIR / "archive"
    archive_dir.mkdir(exist_ok=True)
    try:
        old_version = version_path.read_text(encoding="utf-8").strip() or "v1"
        old_policy = policy_path.read_text(encoding="utf-8")
        (archive_dir / f"intent_policy.{old_version}.md").write_text(old_policy, encoding="utf-8")
    except OSError:
        pass
    # bump version
    new_version = _next_version(old_version)
    policy_path.write_text(new_policy_md, encoding="utf-8")
    version_path.write_text(new_version + "\n", encoding="utf-8")
    log.info("promoted intent policy → %s (eval accuracy=%s)", new_version, eval_snapshot.get("accuracy"))
    return True


def _next_version(old: str) -> str:
    """v1 → v2, vX → v(X+1)."""
    m = re.match(r"v(\d+)", old or "v1")
    if not m:
        return "v2"
    return f"v{int(m.group(1)) + 1}"


def current_model_version() -> str:
    try:
        return (Path(_CONFIG_DIR) / "intent_version.txt").read_text(encoding="utf-8").strip() or "v1"
    except OSError:
        return "v1"
