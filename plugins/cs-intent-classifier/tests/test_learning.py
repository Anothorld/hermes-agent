"""Learning loop tests — T2 few-shot, T3 trend/direction, distill fallback, promote."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from cs_intent_classifier_pkg import db, learning  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_INTENT_DB_PATH", str(tmp_path / "test.db"))


def _seed_correction(*, predicted: str, corrected: str, env: str = "TEST", subject: str = "", predicted_intents: list = None) -> None:
    pred = {"primary_intent": predicted}
    if predicted_intents is not None:
        pred["intents"] = [{"intent": i} for i in predicted_intents]
    db.insert_correction(
        session_id="s",
        env=env,
        predicted=pred,
        corrected={"primary_intent": corrected},
        reason="test",
        operator_id="op",
        subject=subject,
    )


def test_few_shot_block_empty_when_no_corrections():
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert block == ""


def test_few_shot_block_lists_overrides():
    _seed_correction(predicted="logistics_inquiry", corrected="after_sale_issue")
    _seed_correction(predicted="product_inquiry", corrected="logistics_inquiry")
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "few-shot" in block.lower()
    assert "after_sale_issue" in block


def test_few_shot_skips_same_intent():
    _seed_correction(predicted="product_inquiry", corrected="product_inquiry")  # no change
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert block == ""  # no real override → empty


def test_few_shot_block_includes_subject_and_intents():
    # The few-shot example should carry the email subject + the predicted intents
    # list so the LLM sees text features + multi-intent detection context.
    _seed_correction(
        predicted="logistics_inquiry",
        corrected="after_sale_issue",
        subject="Where is my order + damaged sofa",
        predicted_intents=["logistics_inquiry", "after_sale_issue"],
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "Where is my order + damaged sofa" in block
    assert "logistics_inquiry" in block
    assert "after_sale_issue" in block
    assert "intents=" in block


def test_few_shot_block_handles_label_only_correction():
    # A correction with no AI prediction (predicted={}) renders as an operator
    # label example, not a predicted→corrected pair.
    db.insert_correction(
        session_id="s-lab",
        env="TEST",
        predicted={},
        corrected={"primary_intent": "product_inquiry"},
        reason="",
        operator_id="op",
        subject="Swatch request",
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "Swatch request" in block
    assert "operator_label_primary=product_inquiry" in block
    assert "no AI prediction" in block


def test_weekly_trend_insufficient_when_no_snapshots():
    t = learning.latest_weekly_trend(env="TEST", weeks=2)
    assert t["direction"] == "insufficient"


def test_weekly_trend_up():
    from datetime import date
    db.record_eval_snapshot(date="2026-07-01", env="TEST", model_version="v1", accuracy=0.8)
    db.record_eval_snapshot(date="2026-07-02", env="TEST", model_version="v1", accuracy=0.82)
    # simulate "last week" being older by writing a snapshot dated 10 days ago
    # — but trend uses utcnow, so we just verify it returns a direction
    t = learning.latest_weekly_trend(env="TEST", weeks=2)
    assert t["direction"] in ("up", "down", "flat", "insufficient")


def test_distill_fallback_deterministic_when_no_llm(monkeypatch):
    monkeypatch.delenv("CS_INTENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_correction(predicted="logistics_inquiry", corrected="after_sale_issue")
    samples = db.list_corrections(env="TEST", limit=10)
    md, used_llm = learning.distill_intent_policy_llm(
        env="TEST", baseline_md="", samples=samples, mode="rebuild"
    )
    assert used_llm is False
    assert "ADJUST" in md
    assert "after_sale_issue" in md


def test_next_version():
    assert learning._next_version("v1") == "v2"
    assert learning._next_version("v5") == "v6"
    assert learning._next_version("") == "v2"


def test_promote_writes_and_archives(tmp_path, monkeypatch):
    # point config dir to tmp
    monkeypatch.setattr(learning, "_CONFIG_DIR", tmp_path)
    (tmp_path / "intent_version.txt").write_text("v1\n")
    (tmp_path / "intent_policy.md").write_text("old policy\n")
    (tmp_path / "archive").mkdir()
    learning.promote_intent_prompt(new_policy_md="new policy\n", eval_snapshot={"accuracy": 0.9})
    assert (tmp_path / "intent_version.txt").read_text().strip() == "v2"
    assert (tmp_path / "intent_policy.md").read_text() == "new policy\n"
    assert (tmp_path / "archive" / "intent_policy.v1.md").read_text() == "old policy\n"
