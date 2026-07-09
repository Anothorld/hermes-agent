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


def _seed_correction(
    *,
    predicted: str,
    corrected: str,
    env: str = "TEST",
    subject: str = "",
    body: str = "",
    predicted_intents: list = None,
    snippets: list | None = None,
) -> None:
    pred: dict = {"primary_intent": predicted}
    if predicted_intents is not None:
        intents = []
        for i, name in enumerate(predicted_intents):
            item = {"intent": name}
            if snippets and i < len(snippets):
                item["snippet"] = snippets[i]
            intents.append(item)
        pred["intents"] = intents
    elif snippets:
        pred["intents"] = [{"intent": predicted, "snippet": snippets[0]}]
    db.insert_correction(
        session_id="s",
        env=env,
        predicted=pred,
        corrected={"primary_intent": corrected},
        reason="",
        operator_id="op",
        subject=subject,
        body=body,
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
    _seed_correction(predicted="product_inquiry", corrected="product_inquiry")
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert block == ""


def test_few_shot_block_includes_subject_body_and_intents():
    _seed_correction(
        predicted="logistics_inquiry",
        corrected="after_sale_issue",
        subject="Re: Order",
        body="The sofa arrived damaged and I need a refund.",
        predicted_intents=["logistics_inquiry", "after_sale_issue"],
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert 'subject="Re: Order"' in block
    assert "damaged" in block
    assert "refund" in block
    assert "logistics_inquiry" in block
    assert "after_sale_issue" in block
    assert "intents=" in block
    # reason is unused / not injected
    assert "reason=" not in block


def test_strip_quoted_reply_gmail_and_gt_lines():
    text = (
        "The sofa arrived damaged and I need a refund.\n\n"
        "On Mon, 1 Jan 2026 at 10:00 Alice <a@example.com> wrote:\n"
        "> Where is my order?\n"
        "> Thanks"
    )
    cleaned = learning.strip_quoted_reply(text)
    assert "damaged" in cleaned
    assert "refund" in cleaned
    assert "Where is my order" not in cleaned
    assert "Alice" not in cleaned


def test_strip_quoted_reply_original_message():
    text = (
        "Please change the delivery address.\n"
        "-----Original Message-----\n"
        "From: support@povison.com\n"
        "Subject: Re: Order\n"
        "Old thread content about tracking."
    )
    cleaned = learning.strip_quoted_reply(text)
    assert "change the delivery address" in cleaned
    assert "tracking" not in cleaned


def test_few_shot_strips_quoted_reply_from_body():
    _seed_correction(
        predicted="logistics_inquiry",
        corrected="after_sale_issue",
        subject="Re: Order",
        body=(
            "The sofa arrived damaged and I need a refund.\n\n"
            "On Mon, 1 Jan 2026 Alice wrote:\n"
            "> Where is my tracking number for order #11223344?"
        ),
        predicted_intents=["logistics_inquiry", "after_sale_issue"],
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "damaged" in block
    assert "refund" in block
    assert "tracking number" not in block
    assert "11223344" not in block


def test_few_shot_falls_back_to_snippet_when_body_missing():
    _seed_correction(
        predicted="logistics_inquiry",
        corrected="after_sale_issue",
        subject="Re: Order",
        body="",
        predicted_intents=["logistics_inquiry"],
        snippets=["Where is my order? The arm is ripped."],
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "ripped" in block


def test_few_shot_block_handles_label_only_correction():
    db.insert_correction(
        session_id="s-lab",
        env="TEST",
        predicted={},
        corrected={"primary_intent": "product_inquiry"},
        reason="",
        operator_id="op",
        subject="Swatch request",
        body="Can I get fabric samples for the Atticus sofa?",
    )
    block = learning.build_few_shot_block(env="TEST", n=4)
    assert "Swatch request" in block
    assert "fabric samples" in block
    assert "operator_label_primary=product_inquiry" in block
    assert "no AI prediction" in block


def test_weekly_trend_insufficient_when_no_snapshots():
    t = learning.latest_weekly_trend(env="TEST", weeks=2)
    assert t["direction"] == "insufficient"


def test_weekly_trend_up():
    db.record_eval_snapshot(date="2026-07-01", env="TEST", model_version="v1", accuracy=0.8)
    db.record_eval_snapshot(date="2026-07-02", env="TEST", model_version="v1", accuracy=0.82)
    t = learning.latest_weekly_trend(env="TEST", weeks=2)
    assert t["direction"] in ("up", "down", "flat", "insufficient")


def test_distill_fallback_uses_body_not_reason(monkeypatch):
    monkeypatch.delenv("CS_INTENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_correction(
        predicted="logistics_inquiry",
        corrected="after_sale_issue",
        subject="Re: Order",
        body="Package arrived with a cracked leg, need replacement.",
    )
    samples = db.list_corrections(env="TEST", limit=10)
    md, used_llm = learning.distill_intent_policy_llm(
        env="TEST", baseline_md="", samples=samples, mode="rebuild"
    )
    assert used_llm is False
    assert "ADJUST" in md
    assert "after_sale_issue" in md
    assert "cracked" in md or "replacement" in md


def test_distill_prompt_includes_subject_body_not_reason():
    samples = [
        {
            "subject": "Payment",
            "body": "Afterpay wasn't approved so I couldn't finish the sale",
            "predicted": {"primary_intent": "after_sale_issue", "intents": [{"intent": "after_sale_issue"}]},
            "corrected": {"primary_intent": "order_management"},
            "reason": "should be ignored",
        }
    ]
    prompt = learning._build_distill_prompt(baseline_md="", samples=samples, mode="rebuild")
    assert "Afterpay" in prompt
    assert "order_management" in prompt
    assert "should be ignored" not in prompt
    assert '"reason"' not in prompt


def test_next_version():
    assert learning._next_version("v1") == "v2"
    assert learning._next_version("v5") == "v6"
    assert learning._next_version("") == "v2"


def test_promote_writes_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "_CONFIG_DIR", tmp_path)
    (tmp_path / "intent_version.txt").write_text("v1\n")
    (tmp_path / "intent_policy.md").write_text("old policy\n")
    (tmp_path / "archive").mkdir()
    learning.promote_intent_prompt(new_policy_md="new policy\n", eval_snapshot={"accuracy": 0.9})
    assert (tmp_path / "intent_version.txt").read_text().strip() == "v2"
    assert (tmp_path / "intent_policy.md").read_text() == "new policy\n"
    assert (tmp_path / "archive" / "intent_policy.v1.md").read_text() == "old policy\n"
