"""Golden-set evaluator for the intent classifier.

Scores the classifier against ``eval/cases/golden.jsonl`` plus (optionally)
``eval/cases/failures.jsonl`` (auto-synced operator keyword mistakes).

Two layers:
- Deterministic: runs the keyword layer only (fast, no LLM cost).
- LLM (optional): runs the full classify() including LLM fallback.

Case fields:
- ``expected_primary_intent`` / ``expected_in_scope`` — when keyword returns a result.
- ``expected_outcome: "keyword_miss"`` — keyword MUST return None (fallthrough).
  Used by the auto-learning loop to verify soft false-positives are fixed by
  guards/overlays without regressing golden hits.
- ``expect_null: ["field"]`` — fabrication check.

No-fabrication check: cases with ``expect_null`` fail if the classifier returns
a non-null value for that field.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import classifier

log = logging.getLogger(__name__)
_EVAL_DIR = Path(__file__).resolve().parent / "eval" / "cases"


@dataclass
class EvalResult:
    total: int = 0
    correct: int = 0
    per_intent: dict[str, dict[str, int]] = field(default_factory=dict)
    fabrication_failures: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    golden_total: int = 0
    golden_correct: int = 0
    failure_total: int = 0
    failure_correct: int = 0

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0

    @property
    def golden_accuracy(self) -> float:
        return (self.golden_correct / self.golden_total) if self.golden_total else 0.0

    @property
    def failure_accuracy(self) -> float:
        return (self.failure_correct / self.failure_total) if self.failure_total else 0.0


def load_golden() -> list[dict[str, Any]]:
    return _load_jsonl(_EVAL_DIR / "golden.jsonl")


def load_failures() -> list[dict[str, Any]]:
    return _load_jsonl(_EVAL_DIR / "failures.jsonl")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_failure(case: dict[str, Any]) -> None:
    """Append a correction-derived failure case to failures.jsonl (T1)."""
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _EVAL_DIR / "failures.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")


def rewrite_failures(cases: list[dict[str, Any]]) -> None:
    """Atomically rewrite failures.jsonl (used by the sync job for dedupe)."""
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _EVAL_DIR / "failures.jsonl"
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    tmp.replace(path)


def run_eval(
    *,
    use_llm: bool = False,
    include_failures: bool = True,
    cases: Optional[list[dict[str, Any]]] = None,
) -> EvalResult:
    """Run the golden-set (+ optional failures) eval.

    By default runs the keyword layer only (deterministic, fast). Set use_llm=True
    to run the full classify() including LLM fallback (slower, costs tokens).

    When ``include_failures=True`` (default), also scores ``failures.jsonl``.
    Pass ``cases=`` to evaluate an explicit list (skips file loads).
    """
    if cases is not None:
        golden: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        all_cases = list(cases)
        for c in all_cases:
            if c.get("source") == "correction" or c.get("bank") == "failures":
                failures.append(c)
            else:
                golden.append(c)
        # If caller didn't tag, treat all as golden for scoring buckets
        if not golden and not failures:
            golden = all_cases
    else:
        golden = load_golden()
        failures = load_failures() if include_failures else []
        all_cases = list(golden) + list(failures)

    result = EvalResult()
    for case in all_cases:
        is_failure_bank = bool(
            case.get("source") == "correction"
            or case.get("bank") == "failures"
            or case in failures
        )
        ok = _score_one(result, case, use_llm=use_llm)
        if is_failure_bank:
            result.failure_total += 1
            if ok:
                result.failure_correct += 1
        else:
            result.golden_total += 1
            if ok:
                result.golden_correct += 1
    return result


def _score_one(result: EvalResult, case: dict[str, Any], *, use_llm: bool) -> bool:
    result.total += 1
    subject = case.get("subject", "")
    body = case.get("body", "")
    metadata = case.get("metadata") or {}
    expected_primary = case.get("expected_primary_intent")
    expected_in_scope = case.get("expected_in_scope")
    expected_outcome = case.get("expected_outcome")  # e.g. "keyword_miss"
    expect_null = case.get("expect_null") or []

    if use_llm:
        ge = classifier.classify(subject=subject, body=body, metadata=metadata)
    else:
        ge = classifier.keyword_classify(subject=subject, body=body, metadata=metadata)

    # Explicit fallthrough expectation (auto-learning overlay / guard target).
    # For failure-bank cases, also accept a correct primary hit as resolved —
    # the goal is "stop the false positive", not "must miss forever".
    if expected_outcome == "keyword_miss":
        if ge is None:
            correct = True
        elif expected_primary and ge.get("primary_intent") == expected_primary:
            correct = True
        else:
            correct = False
        if correct:
            result.correct += 1
        _tally(result, expected_primary or "keyword_miss", correct=correct)
        result.details.append({
            "case": case.get("name"),
            "expected": "keyword_miss_or_correct",
            "got": None if ge is None else ge.get("primary_intent"),
            "correct": correct,
            "fabrication_ok": True,
        })
        return correct

    if ge is None:
        # keyword miss — counts as incorrect for the deterministic run unless
        # the case expected a miss (handled above).
        result.details.append({"case": case.get("name"), "outcome": "keyword_miss", "correct": False})
        _tally(result, expected_primary or "unknown", correct=False)
        return False

    primary_ok = (ge.get("primary_intent") == expected_primary) if expected_primary else True
    in_scope_ok = (ge.get("in_scope") == expected_in_scope) if expected_in_scope is not None else True
    fab_ok = True
    for nf in expect_null:
        val = _dotted_get(ge, nf)
        if val is not None and val != "" and val != [] and val != {}:
            fab_ok = False
            result.fabrication_failures += 1
            log.warning("fabrication: case=%s field=%s expected null got %r", case.get("name"), nf, val)
    correct = bool(primary_ok and in_scope_ok and fab_ok)
    if correct:
        result.correct += 1
    _tally(result, expected_primary or "unknown", correct=correct)
    result.details.append({
        "case": case.get("name"),
        "expected": expected_primary,
        "got": ge.get("primary_intent"),
        "correct": correct,
        "fabrication_ok": fab_ok,
    })
    return correct


def _tally(result: EvalResult, intent: str, *, correct: bool) -> None:
    bucket = result.per_intent.setdefault(intent, {"correct": 0, "total": 0})
    bucket["total"] += 1
    if correct:
        bucket["correct"] += 1


def _dotted_get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur
