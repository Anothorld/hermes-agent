"""Scheme 1 — automatic keyword failure bank + self-validated overlay loop.

Pipeline (cron ``jobs/optimize_keyword.py`` or on-demand):

1. **Sync** keyword-source operator corrections → ``eval/cases/failures.jsonl``
   (deduped by fingerprint + correction id).
2. **Propose** fallthrough overlays from recurring false-positive phrases
   (deterministic n-gram extraction; no LLM required).
3. **Self-eval** candidate overlays against golden + failures:
   - golden accuracy must not drop below baseline − ``keyword_promote_max_golden_drop``
   - failure-bank false-positive rate must improve (or stay equal with new coverage)
4. **Promote** only when self-eval passes → write ``config/keyword_overlays.yaml``
   and archive the previous file. Rejected candidates are audited, never applied.

Overlays only force soft-block fallthrough (return None → LLM). They never invent
new keyword intents — that keeps the loop precision-safe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from . import classifier, db, eval_runner, learning

log = logging.getLogger(__name__)

_PLUGIN_ROOT = Path(__file__).resolve().parent
_EVAL_DIR = _PLUGIN_ROOT / "eval" / "cases"
_CONFIG_DIR = _PLUGIN_ROOT / "config"
_STATE_PATH = _EVAL_DIR / "keyword_sync_state.json"
_OVERLAYS_PATH = _CONFIG_DIR / "keyword_overlays.yaml"
_FAILURES_PATH = _EVAL_DIR / "failures.jsonl"

_STOPWORDS = frozenset(
    """
    a an the and or but if to of in on for with my your our their this that
    is are was were be been being have has had do does did will would can could
    i me we you he she it they them from at by as not no yes ok hi hello please
    thanks thank just so very really also about into over after before
    """.split()
)

# Soft intents that keyword may falsely claim — overlays only target these FPs.
_SOFT_INTENTS = frozenset(
    {
        "logistics_inquiry",
        "product_inquiry",
        "after_sale_issue",
        "order_management",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_learning_cfg() -> dict[str, Any]:
    return learning._load_learning_config()


def _fingerprint(subject: str, body: str, expected: str) -> str:
    raw = f"{subject.strip().lower()}\n{body.strip().lower()}\n{expected}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_sync_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {"synced_correction_ids": [], "synced_fingerprints": [], "last_sync_at": None}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"synced_correction_ids": [], "synced_fingerprints": [], "last_sync_at": None}
    data.setdefault("synced_correction_ids", [])
    data.setdefault("synced_fingerprints", [])
    return data


def _save_sync_state(state: dict[str, Any]) -> None:
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)
    state["last_sync_at"] = _utcnow()
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_failures() -> list[dict[str, Any]]:
    """Load failure-bank cases (may be empty)."""
    if not _FAILURES_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in _FAILURES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _rewrite_failures(cases: list[dict[str, Any]]) -> None:
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with _FAILURES_PATH.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")


def _is_keyword_false_positive(correction: dict[str, Any]) -> bool:
    """True when AI keyword prediction primary ≠ operator corrected primary."""
    predicted = correction.get("predicted") or {}
    corrected = correction.get("corrected") or {}
    if not predicted:
        return False
    if (predicted.get("classifier_source") or "") != "keyword":
        return False
    pred_p = predicted.get("primary_intent") or ""
    corr_p = corrected.get("primary_intent") or ""
    if not pred_p or not corr_p or pred_p == corr_p:
        return False
    return pred_p in _SOFT_INTENTS


def _body_from_correction(correction: dict[str, Any]) -> str:
    """Best-effort body text for a failure case.

    Prefer the stored correction ``body`` column (Console-supplied). Fall back
    to predicted intent snippets / Chinese summary for older rows. Quoted reply
    content is stripped so overlays are not proposed from prior-thread text.
    """
    from .learning import strip_quoted_reply

    raw = str(correction.get("body") or "").strip()
    if not raw:
        predicted = correction.get("predicted") or {}
        snippets: list[str] = []
        for intent in predicted.get("intents") or []:
            if isinstance(intent, dict) and intent.get("snippet"):
                snippets.append(str(intent["snippet"]))
        if snippets:
            raw = snippets[0]
        else:
            raw = str(predicted.get("summary_zh") or "")
    return strip_quoted_reply(raw)


def _keyword_block_for_intent(intent: str) -> str:
    return {
        "logistics_inquiry": "logistics",
        "product_inquiry": "product",
        "after_sale_issue": "after_sale",
        "order_management": "order_mgmt",
    }.get(intent, "soft")


def sync_keyword_failures(*, env: str, limit: int = 500) -> dict[str, Any]:
    """Append new keyword false-positive corrections into failures.jsonl.

    Idempotent via correction id + content fingerprint. Returns sync stats.
    """
    state = _load_sync_state()
    seen_ids = {int(x) for x in state.get("synced_correction_ids") or []}
    seen_fps = set(state.get("synced_fingerprints") or [])
    existing = load_failures()
    for case in existing:
        fp = case.get("fingerprint")
        if fp:
            seen_fps.add(fp)
        cid = case.get("correction_id")
        if cid is not None:
            try:
                seen_ids.add(int(cid))
            except (TypeError, ValueError):
                pass

    corrections = db.list_corrections(env=env, limit=limit)
    added = 0
    skipped = 0
    for c in corrections:
        if not _is_keyword_false_positive(c):
            skipped += 1
            continue
        cid = c.get("id")
        try:
            cid_int = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid_int = None
        if cid_int is not None and cid_int in seen_ids:
            skipped += 1
            continue

        predicted = c.get("predicted") or {}
        corrected = c.get("corrected") or {}
        subject = str(c.get("subject") or "")
        body = _body_from_correction(c)
        expected = str(corrected.get("primary_intent") or "")
        fp = _fingerprint(subject, body, expected)
        if fp in seen_fps:
            if cid_int is not None:
                seen_ids.add(cid_int)
            skipped += 1
            continue

        pred_primary = str(predicted.get("primary_intent") or "")
        scope = classifier._load_scope()
        # Soft FP goal for the auto-loop: keyword should fall through (LLM),
        # not invent a new hard keyword rule. Score as expected_outcome=keyword_miss.
        case = {
            "name": f"kw_fail_{cid_int or fp}",
            "subject": subject,
            "body": body or subject,
            "metadata": {
                "has_prior_session": bool((predicted.get("conversation_stage") or "") == "follow_up"),
            },
            "expected_primary_intent": expected,
            "expected_in_scope": bool(scope.get(expected, False)),
            "expected_outcome": "keyword_miss",
            "bank": "failures",
            "keyword_block": _keyword_block_for_intent(pred_primary),
            "source": "correction",
            "correction_id": cid_int,
            "session_id": c.get("session_id"),
            "predicted_primary": pred_primary,
            "fingerprint": fp,
            "reason": c.get("reason") or "",
            "synced_at": _utcnow(),
        }
        eval_runner.append_failure(case)
        seen_fps.add(fp)
        if cid_int is not None:
            seen_ids.add(cid_int)
        added += 1

    state["synced_correction_ids"] = sorted(seen_ids)[-2000:]
    state["synced_fingerprints"] = sorted(seen_fps)[-2000:]
    _save_sync_state(state)
    log.info("keyword failure sync env=%s added=%d skipped=%d", env, added, skipped)
    return {"added": added, "skipped": skipped, "bank_size": len(load_failures())}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS and len(t) > 2]


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def propose_fallthrough_overlays(
    *,
    failures: Optional[list[dict[str, Any]]] = None,
    min_support: int = 2,
    max_rules: int = 12,
) -> list[dict[str, Any]]:
    """Derive fallthrough regex overlays from recurring FP phrases.

    Only proposes overlays for soft-block false positives where the keyword
    still returns the wrong primary on the failure text (still an active FP).
    """
    failures = failures if failures is not None else load_failures()
    phrase_counter: Counter[str] = Counter()
    phrase_blocks: dict[str, Counter[str]] = {}
    active_fps: list[dict[str, Any]] = []

    for case in failures:
        subject = str(case.get("subject") or "")
        body = str(case.get("body") or "")
        expected = case.get("expected_primary_intent")
        ge = classifier.keyword_classify(subject=subject, body=body, metadata=case.get("metadata") or {})
        if ge is None:
            continue  # already falling through — no overlay needed
        if ge.get("primary_intent") == expected:
            continue  # already correct
        if (ge.get("primary_intent") or "") not in _SOFT_INTENTS:
            continue
        active_fps.append(case)
        tokens = _tokenize(f"{subject} {body}")
        phrases = _ngrams(tokens, 2) + _ngrams(tokens, 3)
        block = case.get("keyword_block") or _keyword_block_for_intent(str(ge.get("primary_intent") or ""))
        for phrase in phrases:
            phrase_counter[phrase] += 1
            phrase_blocks.setdefault(phrase, Counter())[block] += 1

    rules: list[dict[str, Any]] = []
    for phrase, count in phrase_counter.most_common(80):
        if count < min_support:
            continue
        # Skip phrases that are pure order numbers / SKUs (too specific / noisy).
        if re.fullmatch(r"\d{4,}", phrase.replace(" ", "")):
            continue
        block_counts = phrase_blocks.get(phrase) or Counter()
        block = block_counts.most_common(1)[0][0] if block_counts else "soft"
        escaped = re.escape(phrase)
        # Scope overlay to the specific soft block that produced the FP.
        # Do NOT also tag "soft" — that would fallthrough logistics overlays on
        # product/after_sale matches sharing the same phrase and over-suppress.
        rules.append(
            {
                "id": f"auto_{hashlib.sha1(phrase.encode()).hexdigest()[:10]}",
                "action": "fallthrough",
                "blocks": [block],
                "pattern": rf"\b{escaped}\b",
                "reason": f"auto from keyword FP phrase '{phrase}' (n={count})",
                "support": count,
                "source": "keyword_optimize",
            }
        )
        if len(rules) >= max_rules:
            break

    log.info(
        "proposed %d fallthrough overlays from %d active keyword FPs",
        len(rules),
        len(active_fps),
    )
    return rules


def _read_overlays_file() -> dict[str, Any]:
    if not _OVERLAYS_PATH.exists():
        return {"version": 0, "fallthrough_patterns": [], "updated_at": None}
    try:
        data = yaml.safe_load(_OVERLAYS_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return {"version": 0, "fallthrough_patterns": [], "updated_at": None}
    if not isinstance(data, dict):
        return {"version": 0, "fallthrough_patterns": [], "updated_at": None}
    data.setdefault("version", 0)
    data.setdefault("fallthrough_patterns", [])
    return data


def _write_overlays_file(data: dict[str, Any]) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    archive = _CONFIG_DIR / "archive"
    archive.mkdir(exist_ok=True)
    if _OVERLAYS_PATH.exists():
        ver = int(data.get("version") or 1) - 1
        try:
            old = _OVERLAYS_PATH.read_text(encoding="utf-8")
            (archive / f"keyword_overlays.v{max(ver, 0)}.yaml").write_text(old, encoding="utf-8")
        except OSError:
            pass
    payload = {
        "version": int(data.get("version") or 1),
        "updated_at": _utcnow(),
        "promoted_by": data.get("promoted_by") or "keyword_optimize",
        "eval": data.get("eval") or {},
        "fallthrough_patterns": data.get("fallthrough_patterns") or [],
    }
    _OVERLAYS_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def score_keyword_bank(
    *,
    cases: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Score keyword layer on failure bank.

    A case is a remaining false positive when keyword returns a primary that
    differs from expected. Fallthrough (None) counts as **resolved** for the
    failure bank (LLM will handle) — this matches overlay design.
    """
    cases = cases if cases is not None else load_failures()
    total = 0
    remaining_fp = 0
    resolved = 0
    correct_hit = 0
    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case.get("expected_primary_intent")
        if not expected:
            continue
        total += 1
        ge = classifier.keyword_classify(
            subject=str(case.get("subject") or ""),
            body=str(case.get("body") or ""),
            metadata=case.get("metadata") or {},
        )
        if ge is None:
            resolved += 1
            details.append({"name": case.get("name"), "outcome": "fallthrough"})
            continue
        got = ge.get("primary_intent")
        if got == expected:
            correct_hit += 1
            resolved += 1
            details.append({"name": case.get("name"), "outcome": "correct_hit", "got": got})
        else:
            remaining_fp += 1
            details.append(
                {
                    "name": case.get("name"),
                    "outcome": "false_positive",
                    "got": got,
                    "expected": expected,
                }
            )
    fp_rate = (remaining_fp / total) if total else 0.0
    resolve_rate = (resolved / total) if total else 1.0
    return {
        "total": total,
        "remaining_fp": remaining_fp,
        "resolved": resolved,
        "correct_hit": correct_hit,
        "fp_rate": fp_rate,
        "resolve_rate": resolve_rate,
        "details": details,
    }


def evaluate_overlay_candidate(
    *,
    candidate_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare baseline vs candidate overlays on golden + failure bank.

    Promote gate (all must hold):
    - **golden_accuracy**(candidate) >= golden_accuracy(baseline) − max_drop
      (never use combined accuracy — failure-bank wins must not mask golden drops)
    - failure fp_rate(candidate) <= failure fp_rate(baseline)
    - resolve_rate must not decrease when adding rules
    - at least one real improvement when new rules are added
    """
    cfg = _load_learning_cfg()
    max_drop = float(cfg.get("keyword_promote_max_golden_drop", 0.0))
    baseline_file = _read_overlays_file()
    baseline_rules = list(baseline_file.get("fallthrough_patterns") or [])

    # Merge: keep existing + add new ids not already present
    existing_ids = {str(r.get("id")) for r in baseline_rules if isinstance(r, dict)}
    merged = list(baseline_rules)
    for rule in candidate_rules:
        rid = str(rule.get("id") or "")
        if rid and rid in existing_ids:
            continue
        # Skip rules that also list "soft" alone without a specific block when
        # the phrase is too generic — still allow; golden gate is the safety net.
        merged.append(rule)

    def _with_rules(rules: list[dict[str, Any]], fn):
        original = classifier._load_keyword_overlays

        def _fake() -> list[dict[str, Any]]:
            return rules

        classifier._load_keyword_overlays = _fake  # type: ignore[assignment]
        try:
            return fn()
        finally:
            classifier._load_keyword_overlays = original  # type: ignore[assignment]

    # CRITICAL: score golden in isolation. Using include_failures=True lets a
    # large failure bank outweigh a golden regression and falsely promote.
    base_golden = _with_rules(
        baseline_rules, lambda: eval_runner.run_eval(use_llm=False, include_failures=False)
    )
    cand_golden = _with_rules(
        merged, lambda: eval_runner.run_eval(use_llm=False, include_failures=False)
    )
    base_bank = _with_rules(baseline_rules, score_keyword_bank)
    cand_bank = _with_rules(merged, score_keyword_bank)

    base_g = base_golden.golden_accuracy if base_golden.golden_total else base_golden.accuracy
    cand_g = cand_golden.golden_accuracy if cand_golden.golden_total else cand_golden.accuracy
    golden_ok = cand_g + 1e-9 >= base_g - max_drop
    fp_ok = cand_bank["fp_rate"] <= base_bank["fp_rate"] + 1e-9
    resolve_ok = cand_bank["resolve_rate"] + 1e-9 >= base_bank["resolve_rate"]
    improved = cand_bank["fp_rate"] < base_bank["fp_rate"] - 1e-9 or (
        cand_bank["resolve_rate"] > base_bank["resolve_rate"] + 1e-9
    )
    new_rule_count = len(merged) - len(baseline_rules)
    promote = bool(golden_ok and fp_ok and resolve_ok and improved and new_rule_count > 0)

    return {
        "promote": promote,
        "golden_ok": golden_ok,
        "fp_ok": fp_ok,
        "resolve_ok": resolve_ok,
        "improved": improved,
        "new_rule_count": new_rule_count,
        "baseline_golden_accuracy": base_g,
        "candidate_golden_accuracy": cand_g,
        "baseline_fp_rate": base_bank["fp_rate"],
        "candidate_fp_rate": cand_bank["fp_rate"],
        "baseline_resolve_rate": base_bank["resolve_rate"],
        "candidate_resolve_rate": cand_bank["resolve_rate"],
        "merged_rules": merged,
        "baseline_rule_count": len(baseline_rules),
    }


def promote_overlays(
    *,
    merged_rules: list[dict[str, Any]],
    eval_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Write promoted overlays to disk (archives previous)."""
    current = _read_overlays_file()
    new_version = int(current.get("version") or 0) + 1
    payload = {
        "version": new_version,
        "promoted_by": "keyword_optimize",
        "eval": {
            "golden_accuracy": eval_snapshot.get("candidate_golden_accuracy"),
            "fp_rate": eval_snapshot.get("candidate_fp_rate"),
            "resolve_rate": eval_snapshot.get("candidate_resolve_rate"),
            "baseline_golden_accuracy": eval_snapshot.get("baseline_golden_accuracy"),
            "baseline_fp_rate": eval_snapshot.get("baseline_fp_rate"),
        },
        "fallthrough_patterns": merged_rules,
    }
    _write_overlays_file(payload)
    log.info(
        "promoted keyword overlays v%s rules=%d golden=%.3f fp=%.3f",
        new_version,
        len(merged_rules),
        float(eval_snapshot.get("candidate_golden_accuracy") or 0),
        float(eval_snapshot.get("candidate_fp_rate") or 0),
    )
    return {"version": new_version, "rule_count": len(merged_rules)}


def run_keyword_optimize_cycle(*, env: str = "LIVE") -> dict[str, Any]:
    """Full automatic loop: sync → propose → self-eval → promote|reject."""
    cfg = _load_learning_cfg()
    min_support = int(cfg.get("keyword_overlay_min_support", 2))
    max_rules = int(cfg.get("keyword_overlay_max_rules", 12))

    sync_stats = sync_keyword_failures(env=env)
    proposals = propose_fallthrough_overlays(min_support=min_support, max_rules=max_rules)
    if not proposals:
        bank = score_keyword_bank()
        golden = eval_runner.run_eval(use_llm=False, include_failures=False)
        return {
            "status": "noop",
            "reason": "no_new_overlay_proposals",
            "sync": sync_stats,
            "golden_accuracy": golden.golden_accuracy if golden.golden_total else golden.accuracy,
            "bank": {k: bank[k] for k in ("total", "remaining_fp", "fp_rate", "resolve_rate")},
            "promoted": False,
        }

    verdict = evaluate_overlay_candidate(candidate_rules=proposals)
    if verdict["promote"] and verdict["new_rule_count"] > 0:
        promo = promote_overlays(merged_rules=verdict["merged_rules"], eval_snapshot=verdict)
        return {
            "status": "promoted",
            "sync": sync_stats,
            "proposals": len(proposals),
            "promoted": True,
            "promo": promo,
            "verdict": {k: v for k, v in verdict.items() if k != "merged_rules"},
        }

    return {
        "status": "rejected" if proposals else "noop",
        "reason": "self_eval_gate_failed" if proposals else "no_proposals",
        "sync": sync_stats,
        "proposals": len(proposals),
        "promoted": False,
        "verdict": {k: v for k, v in verdict.items() if k != "merged_rules"},
    }
