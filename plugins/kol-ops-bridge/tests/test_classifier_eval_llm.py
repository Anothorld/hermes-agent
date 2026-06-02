"""LLM eval helpers (no live API calls)."""

from __future__ import annotations


def test_compare_expect_llm_offer_key(bridge_pkg):
    cer = bridge_pkg.classifier_eval_runner
    case = {
        "id": "x",
        "expect_llm": {
            "facts_extracted": {"offer": {"offer.interest_signal": "needs_more_info"}},
            "signals_include": ["asks_budget"],
        },
    }
    ok, _ = cer.compare_expect_llm(
        case,
        {
            "facts_extracted": {"offer": {"offer.interest_signal": "needs_more_info"}},
            "signals": [{"name": "asks_budget", "confidence": 0.9}],
        },
    )
    assert ok


def test_run_llm_eval_with_mock_runner(bridge_pkg):
    cer = bridge_pkg.classifier_eval_runner
    cases = [
        {
            "id": "mock",
            "input_signals": [{"name": "asks_budget", "confidence": 0.8}],
            "expect_sanitize": {"offer": {"offer.interest_signal": "needs_more_info"}},
        },
    ]

    def _runner(_prompt: str) -> str:
        return """{
          "facts_extracted": {"offer": {"offer.interest_signal": "needs_more_info"}},
          "signals": [{"name": "asks_budget", "confidence": 0.85, "evidence": "budget?"}]
        }"""

    report = cer.run_llm_eval(cases, runner=_runner)
    assert report["failed"] == 0
    assert report["passed"] == 1


def test_build_classifier_prompt_from_case(bridge_pkg):
    cer = bridge_pkg.classifier_eval_runner
    prompt = cer.build_classifier_prompt({
        "id": "interest_inquiry_downgrade",
        "input_signals": [{"name": "asks_deliverables", "confidence": 0.9}],
        "expect_llm": {"facts_extracted": {"offer": {}}},
    })
    assert prompt is not None
    assert "interest_inquiry_downgrade" in prompt
