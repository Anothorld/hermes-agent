"""Golden-set evaluator for the intent classifier.

Scores the classifier against cases/eval/cases/golden.jsonl. Two layers:
- Deterministic: runs the keyword layer only (fast, no LLM cost).
- LLM (optional): runs the full classify() including LLM fallback.

No-fabrication check: cases with `expect_null: ["field"]` fail if the classifier
returns a non-null value for that field (detects fabrication).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0


def load_golden() -> list[dict[str, Any]]:
    path = _EVAL_DIR / "golden.jsonl"
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


def run_eval(*, use_llm: bool = False) -> EvalResult:
    """Run the golden-set eval. Returns EvalResult with accuracy + per-intent + fabrication checks.

    By default runs the keyword layer only (deterministic, fast). Set use_llm=True
    to run the full classify() including LLM fallback (slower, costs tokens).
    """
    cases = load_golden()
    result = EvalResult()
    for case in cases:
        result.total += 1
        subject = case.get("subject", "")
        body = case.get("body", "")
        metadata = case.get("metadata") or {}
        expected_primary = case.get("expected_primary_intent")
        expected_in_scope = case.get("expected_in_scope")
        expect_null = case.get("expect_null") or []

        if use_llm:
            ge = classifier.classify(subject=subject, body=body, metadata=metadata)
        else:
            ge = classifier.keyword_classify(subject=subject, body=body, metadata=metadata)
            if ge is None:
                # keyword miss — counts as incorrect for the deterministic run
                result.details.append({"case": case.get("name"), "outcome": "keyword_miss"})
                _tally(result, expected_primary or "unknown", correct=False)
                continue

        # primary intent check
        primary_ok = (ge.get("primary_intent") == expected_primary) if expected_primary else True
        in_scope_ok = (ge.get("in_scope") == expected_in_scope) if expected_in_scope is not None else True
        # fabrication check
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
    return result


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
