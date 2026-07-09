"""Learning loop for cs-intent-classifier.

T1 error bank + T2 few-shot injection + T3 offline policy distillation.

- T1: golden + failure cases in eval/cases/*.jsonl; eval_runner scores them.
- T2: build_few_shot_block() — recent corrections (subject + body text + labels)
  injected into the classifier prompt as few-shot examples (online, no approve).
  Correction ``reason`` is unused (Console no longer collects it).
- T3: distill_intent_policy_llm() — cron aggregates corrections (with email text)
  and asks an LLM to generate/update config/intent_policy.md rules. Self-adaptive:
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
    float_keys = (
        "promote_min_accuracy_delta",
        "rebuild_threshold",
        "keyword_promote_max_golden_drop",
    )
    int_keys = (
        "fewshot_sample_size",
        "keyword_overlay_min_support",
        "keyword_overlay_max_rules",
    )
    for key in (
        "distill_period",
        "eval_period",
        "promote_min_accuracy_delta",
        "rebuild_threshold",
        "fewshot_period",
        "fewshot_sample_size",
        "keyword_optimize_period",
        "keyword_overlay_min_support",
        "keyword_overlay_max_rules",
        "keyword_promote_max_golden_drop",
    ):
        env_key = f"CS_INTENT_{key.upper()}"
        if env_key in os.environ:
            raw = os.environ[env_key]
            try:
                if key in float_keys:
                    data[key] = float(raw)
                elif key in int_keys:
                    data[key] = int(raw)
                else:
                    data[key] = raw
            except ValueError:
                pass
    return data


# ── T1: corrections listing ──


def list_intent_corrections(*, env: str, since: str = "", until: str = "", limit: int = 200) -> list[dict[str, Any]]:
    return db.list_corrections(env=env, since=since, until=until, limit=limit)


# ── T2: few-shot block ──

_FEWSHOT_BODY_MAX = 280
_DISTILL_BODY_MAX = 400

# Quote / forward markers — aligned with cs-ops-bridge intent_gate._strip_quoted_reply.
_QUOTE_CUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*on\s.{5,200}?\swrote\s*:"),
    re.compile(r"(?im)-{5,}\s*forwarded message\s*-{5,}"),
    re.compile(r"(?im)-{2,}\s*original\s+message\s*-{2,}"),
    re.compile(r"(?im)^\s*from\s*:.*\n(.*\n){0,5}(subject|date|to)\s*:"),
    re.compile(r"(?im)^\s*.{0,80}写道\s*[:：]"),
    re.compile(r"(?im)^\s*-{2,}\s*原始邮件\s*-{2,}"),
)
_QUOTE_LINE_RE = re.compile(r"^\s*>")


def strip_quoted_reply(text: str) -> str:
    """Remove quoted / forwarded reply content from an email body.

    Keeps only the customer's new content so T2/T3 learning does not absorb
    prior-thread noise (agent replies, old questions, signatures in quotes).
    """
    import html as _html

    if not text:
        return ""
    text = _html.unescape(str(text))
    for pat in _QUOTE_CUT_PATTERNS:
        m = pat.search(text)
        if m:
            text = text[: m.start()].rstrip()
    text = "\n".join(
        line for line in text.splitlines() if not _QUOTE_LINE_RE.match(line)
    )
    return text.strip()


def _clip_text(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _correction_body_text(correction: dict[str, Any]) -> str:
    """Best-effort email body for learning samples.

    Prefer the stored ``body`` column. For older rows (pre-body migration),
    fall back to predicted intent snippets so historical corrections still
    contribute text features without requiring a Console re-submit.

    Quoted / forwarded reply blocks are stripped so few-shot / distill do not
    learn from prior-thread noise.
    """
    raw = str(correction.get("body") or "").strip()
    if not raw:
        predicted = correction.get("predicted") or {}
        snippets: list[str] = []
        for intent in predicted.get("intents") or []:
            if isinstance(intent, dict) and intent.get("snippet"):
                snippets.append(str(intent["snippet"]).strip())
        raw = " ".join(s for s in snippets if s)
    return strip_quoted_reply(raw)


def build_few_shot_block(*, env: str, n: int = 0) -> str:
    """Render recent corrections as a few-shot prompt block for the LLM.

    Injected into the classifier LLM prompt at call time. Returns "" when no
    useful corrections are available. Each example prefers **subject + body**
    text features (not label↔label only). Correction ``reason`` is ignored —
    the Console no longer collects it.
    """
    if n <= 0:
        n = int(_load_learning_config().get("fewshot_sample_size", 8))
    corrections = db.list_corrections(env=env, limit=n * 3)
    examples: list[dict[str, Any]] = []
    for c in corrections:
        pred = c.get("predicted") or {}
        corr = c.get("corrected") or {}
        if (pred.get("primary_intent") or "") == (corr.get("primary_intent") or ""):
            continue
        pred_intents = [i.get("intent") for i in (pred.get("intents") or [])]
        examples.append({
            "subject": (c.get("subject") or "").strip()[:120],
            "body": _clip_text(_correction_body_text(c), _FEWSHOT_BODY_MAX),
            "predicted_primary": pred.get("primary_intent"),
            "corrected_primary": corr.get("primary_intent"),
            "predicted_intents": pred_intents,
        })
        if len(examples) >= n:
            break
    if not examples:
        return ""
    lines = [
        "## Few-shot corrections (recent operator overrides — learn from email text + labels)"
    ]
    for ex in examples:
        bits: list[str] = []
        if ex["subject"]:
            bits.append(f'subject="{ex["subject"]}"')
        if ex["body"]:
            bits.append(f'body="{ex["body"]}"')
        text_prefix = (" " + " ".join(bits)) if bits else ""
        pred_prim = ex["predicted_primary"]
        if pred_prim:
            intents_str = f' intents={ex["predicted_intents"]}' if ex["predicted_intents"] else ""
            lines.append(
                f'-{text_prefix} predicted_primary={pred_prim}{intents_str} → corrected_primary={ex["corrected_primary"]}'
            )
        else:
            lines.append(
                f'-{text_prefix} operator_label_primary={ex["corrected_primary"]} (no AI prediction was stored for this email)'
            )
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
                "subject": (c.get("subject") or "").strip()[:120],
                "body": _clip_text(_correction_body_text(c), _DISTILL_BODY_MAX),
                "predicted": (c.get("predicted") or {}).get("primary_intent"),
                "corrected": (c.get("corrected") or {}).get("primary_intent"),
                "predicted_intents": [
                    i.get("intent") for i in ((c.get("predicted") or {}).get("intents") or [])
                    if isinstance(i, dict) and i.get("intent")
                ],
            }
            for c in samples
        ],
        ensure_ascii=False,
        indent=2,
    )
    if mode == "rebuild":
        baseline_section = "Rebuild mode: ignore any prior policy. Generate fresh rules from the samples."
    else:
        baseline_section = (
            "Repair mode: edit the baseline with ADJUST:/REMOVE: prefixes.\n\n"
            f"CURRENT baseline:\n{baseline_md or '(empty)'}"
        )
    return (
        "You analyze operator intent corrections to improve an email intent classifier.\n"
        "Each sample includes subject/body text when available — ground rules in those "
        "text signals (keywords, phrases, checkout vs after-sale cues), not bare label pairs.\n"
        "Produce markdown rules for the classifier. Use ADJUST: to revise or add a rule, "
        "REMOVE: to delete one.\n"
        "Cite evidence (predicted→corrected + short text cue). Do not invent rules not "
        "supported by the samples. Do not rely on a free-text 'reason' field — it is unused.\n"
        f"{baseline_section}\n\n"
        "Output ONLY markdown starting with `## Approved intent policy`. 3-8 bullets.\n\n"
        f"SAMPLES_JSON:\n{samples_block}"
    )


def _deterministic_aggregate(samples: list[dict[str, Any]]) -> str:
    """Fallback: tally predicted→corrected transitions with optional text cues."""
    counts: dict[tuple[str, str], int] = {}
    cues: dict[tuple[str, str], str] = {}
    for c in samples:
        pred = (c.get("predicted") or {}).get("primary_intent") or "?"
        corr = (c.get("corrected") or {}).get("primary_intent") or "?"
        key = (pred, corr)
        counts[key] = counts.get(key, 0) + 1
        if key not in cues:
            body = _clip_text(_correction_body_text(c), 80)
            subj = (c.get("subject") or "").strip()[:60]
            cue = body or subj
            if cue:
                cues[key] = cue
    lines = ["## Approved intent policy (deterministic fallback)"]
    for (pred, corr), n in sorted(counts.items(), key=lambda x: -x[1])[:8]:
        cue = cues.get((pred, corr))
        if cue:
            lines.append(
                f'- ADJUST: when predicted={pred} but email text resembles "{cue}", prefer {corr} (n={n})'
            )
        else:
            lines.append(
                f"- ADJUST: when predicted={pred} but email signals {corr}, prefer {corr} (n={n})"
            )
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
