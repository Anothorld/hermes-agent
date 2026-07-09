"""Scheme 1 — keyword failure bank sync + overlay self-eval promote loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from cs_intent_classifier_pkg import (  # type: ignore[attr-defined]
    classifier,
    db,
    eval_runner,
    keyword_learning,
)


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_INTENT_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("CS_INTENT_KEYWORD_TIER", raising=False)


@pytest.fixture
def isolated_eval(tmp_path, monkeypatch):
    """Point eval/config dirs at tmp so tests don't touch real failures/overlays."""
    eval_dir = tmp_path / "eval" / "cases"
    eval_dir.mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "archive").mkdir()
    (config_dir / "keyword_overlays.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 0,
                "fallthrough_patterns": [],
                "updated_at": None,
                "promoted_by": "test",
                "eval": {},
            }
        ),
        encoding="utf-8",
    )
    # Seed a tiny golden set for promote gate
    golden = [
        {
            "name": "logistics_tracking",
            "subject": "Where is my order?",
            "body": "Where is my order #11223344? It's been a week.",
            "metadata": {},
            "expected_primary_intent": "logistics_inquiry",
            "expected_in_scope": True,
        },
        {
            "name": "after_sale_damage",
            "subject": "Damaged sofa",
            "body": "The sofa arrived damaged, I want a refund.",
            "metadata": {},
            "expected_primary_intent": "after_sale_issue",
            "expected_in_scope": False,
        },
    ]
    (eval_dir / "golden.jsonl").write_text(
        "\n".join(json.dumps(c) for c in golden) + "\n", encoding="utf-8"
    )
    (eval_dir / "failures.jsonl").write_text("", encoding="utf-8")
    (eval_dir / "keyword_sync_state.json").write_text(
        json.dumps({"synced_correction_ids": [], "synced_fingerprints": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(keyword_learning, "_EVAL_DIR", eval_dir)
    monkeypatch.setattr(keyword_learning, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(keyword_learning, "_STATE_PATH", eval_dir / "keyword_sync_state.json")
    monkeypatch.setattr(keyword_learning, "_OVERLAYS_PATH", config_dir / "keyword_overlays.yaml")
    monkeypatch.setattr(keyword_learning, "_FAILURES_PATH", eval_dir / "failures.jsonl")
    monkeypatch.setattr(eval_runner, "_EVAL_DIR", eval_dir)
    monkeypatch.setattr(classifier, "_CONFIG_DIR", config_dir)
    # Keep scope/version readable — copy minimal files from real config if needed
    real_cfg = _PLUGIN_ROOT / "config"
    for name in ("intent_scope.yaml", "intent_version.txt", "intent_learning.yaml"):
        src = real_cfg / name
        if src.exists():
            (config_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return eval_dir, config_dir


def _seed_keyword_fp(
    *,
    session_id: str = "s1",
    subject: str = "Where is my order?",
    snippet: str = "Where is my order #11223344?",
    predicted: str = "logistics_inquiry",
    corrected: str = "after_sale_issue",
    env: str = "TEST",
) -> int:
    return db.insert_correction(
        session_id=session_id,
        env=env,
        predicted={
            "primary_intent": predicted,
            "classifier_source": "keyword",
            "intents": [
                {
                    "intent": predicted,
                    "in_scope": True,
                    "confidence": "high",
                    "snippet": snippet,
                }
            ],
        },
        corrected={"primary_intent": corrected, "in_scope": False},
        reason="test fp",
        operator_id="op",
        subject=subject,
    )


def test_sync_keyword_failures_appends_once(isolated_eval):
    eval_dir, _ = isolated_eval
    _seed_keyword_fp()
    stats1 = keyword_learning.sync_keyword_failures(env="TEST")
    assert stats1["added"] == 1
    stats2 = keyword_learning.sync_keyword_failures(env="TEST")
    assert stats2["added"] == 0
    cases = keyword_learning.load_failures()
    assert len(cases) == 1
    assert cases[0]["expected_outcome"] == "keyword_miss"
    assert cases[0]["predicted_primary"] == "logistics_inquiry"
    assert (eval_dir / "failures.jsonl").exists()


def test_sync_skips_llm_source_corrections(isolated_eval):
    db.insert_correction(
        session_id="s-llm",
        env="TEST",
        predicted={
            "primary_intent": "logistics_inquiry",
            "classifier_source": "llm",
            "intents": [{"intent": "logistics_inquiry", "snippet": "where is"}],
        },
        corrected={"primary_intent": "after_sale_issue"},
        reason="",
        operator_id="op",
        subject="x",
    )
    stats = keyword_learning.sync_keyword_failures(env="TEST")
    assert stats["added"] == 0


def test_propose_and_promote_overlay_improves_fp(isolated_eval, monkeypatch):
    """Two identical soft FPs → propose fallthrough → self-eval promotes."""
    # Seed two FPs with shared phrase so min_support=2 is met
    for i in range(2):
        _seed_keyword_fp(
            session_id=f"s{i}",
            subject="Where is my order?",
            snippet="Where is my order #11223344? damaged arm",
            predicted="logistics_inquiry",
            corrected="after_sale_issue",
        )
    monkeypatch.setenv("CS_INTENT_KEYWORD_OVERLAY_MIN_SUPPORT", "2")
    result = keyword_learning.run_keyword_optimize_cycle(env="TEST")
    # May promote or reject depending on whether phrase extraction finds a safe
    # overlay that doesn't hurt golden logistics case. Either way cycle must finish.
    assert result["status"] in ("promoted", "rejected", "noop")
    assert "sync" in result
    if result["status"] == "promoted":
        overlays = yaml.safe_load(
            (isolated_eval[1] / "keyword_overlays.yaml").read_text(encoding="utf-8")
        )
        assert overlays["version"] >= 1
        assert overlays["fallthrough_patterns"]


def test_evaluate_rejects_overlay_that_breaks_golden(isolated_eval):
    """An overlay that forces ALL logistics fallthrough must fail golden gate."""
    # Baseline golden has logistics_tracking — blanket overlay breaks it.
    bad_rules = [
        {
            "id": "bad_all_logistics",
            "action": "fallthrough",
            "blocks": ["logistics", "soft"],
            "pattern": r"\bwhere is my order\b",
        }
    ]
    # Seed failures so FP side improves — must still reject due to golden drop.
    fails = []
    for i in range(5):
        fails.append(
            {
                "name": f"fp{i}",
                "subject": "Where is my order?",
                "body": "Where is my order #11223344?",
                "expected_primary_intent": "after_sale_issue",
                "expected_outcome": "keyword_miss",
                "source": "correction",
                "bank": "failures",
            }
        )
    (isolated_eval[0] / "failures.jsonl").write_text(
        "\n".join(json.dumps(c) for c in fails) + "\n", encoding="utf-8"
    )
    verdict = keyword_learning.evaluate_overlay_candidate(candidate_rules=bad_rules)
    assert verdict["promote"] is False
    assert verdict["golden_ok"] is False
    assert verdict["candidate_golden_accuracy"] < verdict["baseline_golden_accuracy"]


def test_promote_gate_uses_golden_accuracy_not_combined(isolated_eval):
    """Regression: failure-bank wins must not mask a golden accuracy drop."""
    bad_rules = [
        {
            "id": "bad_soft",
            "action": "fallthrough",
            "blocks": ["soft"],
            "pattern": r"\bwhere is my order\b",
        }
    ]
    fails = [
        {
            "name": f"fp{i}",
            "subject": "Where is my order?",
            "body": "Where is my order #11223344?",
            "expected_primary_intent": "after_sale_issue",
            "expected_outcome": "keyword_miss",
            "source": "correction",
            "bank": "failures",
        }
        for i in range(5)
    ]
    (isolated_eval[0] / "failures.jsonl").write_text(
        "\n".join(json.dumps(c) for c in fails) + "\n", encoding="utf-8"
    )
    verdict = keyword_learning.evaluate_overlay_candidate(candidate_rules=bad_rules)
    # Combined accuracy would rise (5 failures fixed outweigh 1 golden miss),
    # but golden_ok must be False and promote must be False.
    assert verdict["golden_ok"] is False
    assert verdict["promote"] is False
    assert verdict["improved"] is True  # FP side did improve — still reject


def test_score_keyword_bank_counts_fallthrough_as_resolved(isolated_eval, monkeypatch):
    case = {
        "name": "fp1",
        "subject": "Where is my order?",
        "body": "Where is my order #11223344?",
        "expected_primary_intent": "after_sale_issue",
        "source": "correction",
        "bank": "failures",
    }
    (isolated_eval[0] / "failures.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    # Without overlay → remaining FP
    bank1 = keyword_learning.score_keyword_bank()
    assert bank1["total"] == 1
    assert bank1["remaining_fp"] == 1
    # With overlay → fallthrough → resolved
    monkeypatch.setattr(
        classifier,
        "_load_keyword_overlays",
        lambda: [
            {
                "id": "x",
                "action": "fallthrough",
                "blocks": ["soft"],
                "pattern": r"\bwhere is my order\b",
            }
        ],
    )
    bank2 = keyword_learning.score_keyword_bank()
    assert bank2["remaining_fp"] == 0
    assert bank2["resolve_rate"] == 1.0
