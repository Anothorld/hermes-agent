"""Tests for deterministic escalation-rule matching + reply-draft enrichment."""

from __future__ import annotations

import pytest


RULES_MD = """
max_escalation_depth: 5

### rule_id: package_lost
- signals_match: ["not_received", "no_tracking"]
- severity: blocking
- suggested_question: "The KOL says the package never arrived. Reship or refund?"
- required_facts_to_resume: ["fulfillment.reship_decision"]

### rule_id: off_cap_price
- signals_match: ["paid_quote_over_ceiling"]
- severity: critical
- suggested_question: "KOL quote exceeds the cap. Approve a higher budget?"
"""


def _pol(bridge_pkg):
    return bridge_pkg.policies


def test_no_signals_no_hint(bridge_pkg):
    parsed = _pol(bridge_pkg).parse_escalation_rules(RULES_MD)
    out = _pol(bridge_pkg).match_escalation_rules(parsed, [])
    assert out["should_consider"] is False
    assert out["max_escalation_depth"] == 5


def test_subset_match_wins(bridge_pkg):
    parsed = _pol(bridge_pkg).parse_escalation_rules(RULES_MD)
    out = _pol(bridge_pkg).match_escalation_rules(
        parsed, ["not_received", "no_tracking", "angry"])
    assert out["should_consider"] is True
    assert out["matched_rule_id"] == "package_lost"
    assert out["severity"] == "blocking"
    assert out["required_facts_to_resume"] == ["fulfillment.reship_decision"]


def test_partial_match_does_not_fire(bridge_pkg):
    parsed = _pol(bridge_pkg).parse_escalation_rules(RULES_MD)
    out = _pol(bridge_pkg).match_escalation_rules(parsed, ["not_received"])
    assert out["should_consider"] is False


def test_accepts_signal_dicts(bridge_pkg):
    parsed = _pol(bridge_pkg).parse_escalation_rules(RULES_MD)
    out = _pol(bridge_pkg).match_escalation_rules(
        parsed, [{"name": "paid_quote_over_ceiling", "confidence": 0.9}])
    assert out["matched_rule_id"] == "off_cap_price"


def test_first_rule_in_order_wins(bridge_pkg):
    parsed = _pol(bridge_pkg).parse_escalation_rules(RULES_MD)
    out = _pol(bridge_pkg).match_escalation_rules(
        parsed, ["not_received", "no_tracking", "paid_quote_over_ceiling"])
    assert out["matched_rule_id"] == "package_lost"


# ---- reply-draft enrichment ----------------------------------------------


def _rd(bridge_pkg):
    return bridge_pkg.reply_draft


def test_enrich_fills_to_and_re_subject(bridge_pkg):
    merged = _rd(bridge_pkg).enrich_envelope(
        {"body": "Sounds great!"},
        {"from": "alice@example.com", "subject": "Collab?", "thread_id": "t1"},
    )
    assert merged["to"] == "alice@example.com"
    assert merged["subject"] == "Re: Collab?"
    assert merged["thread_id"] == "t1"


def test_enrich_keeps_existing_re_prefix(bridge_pkg):
    merged = _rd(bridge_pkg).enrich_envelope(
        {"body": "ok"},
        {"from_addr": "b@x.com", "subject": "Re: Deal"},
    )
    assert merged["subject"] == "Re: Deal"
    assert merged["to"] == "b@x.com"


def test_enrich_missing_recipient_raises(bridge_pkg):
    with pytest.raises(bridge_pkg.reply_draft.ReplyDraftError):
        _rd(bridge_pkg).enrich_envelope({"body": "hi"}, {"subject": "x"})


def test_enrich_empty_body_raises(bridge_pkg):
    with pytest.raises(bridge_pkg.reply_draft.ReplyDraftError):
        _rd(bridge_pkg).enrich_envelope({"body": "  "}, {"from": "a@x.com"})
