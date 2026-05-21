"""Audit D1 — email classifier rule-match coverage.

The runtime classifier lives in two places:
1. ``policies.parse_escalation_rules`` — converts the markdown rule book
   into ``[{id, signals_match, severity, suggested_question, ...}, ...]``.
2. A skill-side step that, for each parsed rule, checks whether any of
   the dispatcher-extracted signals (``namespace.predicate`` strings)
   intersects ``signals_match``. The first matching rule wins.

This test fixes the contract for step 2 by exercising 11 representative
inbound-email fixtures against a minimal Python implementation of the
match. The implementation is intentionally inlined so a regression in
the markdown rule shape surfaces here.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any


def _policies():
    key = "kol_ops_bridge_pkg.policies"
    if key in sys.modules:
        return sys.modules[key]
    pkg_name = "kol_ops_bridge_pkg"
    plugin_root = Path(__file__).resolve().parents[1]
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg
    pkg = sys.modules[pkg_name]
    # Mirror conftest._load_package so other tests sharing this worker
    # still find kol_ops_bridge_pkg.cal etc.
    for sub in ("schema", "goals", "policies", "cal", "discovery_router"):
        sub_key = f"{pkg_name}.{sub}"
        if sub_key in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(sub_key, plugin_root / f"{sub}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[sub_key] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return sys.modules[key]


RULES_MD = """
max_escalation_depth: 3

### rule_id: paid_quote_over_ceiling
- signals_match: ["compensation.kol_quoted_over_ceiling", "compensation.over_budget"]
- severity: high
- suggested_question: "KOL quote exceeds paid_ceiling — approve override?"
- required_facts_to_resume: ["paid_ceiling_override"]

### rule_id: contract_change_request
- signals_match: ["contract.change_request", "contract.exclusivity_objection"]
- severity: normal
- suggested_question: "KOL wants to renegotiate contract terms — accept?"
- required_facts_to_resume: []

### rule_id: off_whitelist_sku
- signals_match: ["product.off_whitelist_request"]
- severity: high
- suggested_question: "KOL wants SKU not in whitelist — allow?"
- required_facts_to_resume: ["sku_override"]

### rule_id: shipping_address_change
- signals_match: ["shipping.address_change_after_lock"]
- severity: normal
- suggested_question: "Shipping address change after lock — reissue?"
- required_facts_to_resume: []

### rule_id: content_brand_safety
- signals_match: ["content.brand_safety_concern", "content.disclosure_missing"]
- severity: high
- suggested_question: "Content has brand-safety issues — block publish?"
- required_facts_to_resume: []
"""


def _signal_match(rules: list[dict[str, Any]], signals: list[str]) -> str | None:
    """First rule whose signals_match ∩ signals is non-empty."""
    sigset = set(signals)
    for rule in rules:
        wanted = set(rule.get("signals_match") or [])
        if wanted & sigset:
            return rule["id"]
    return None


def test_classifier_eleven_fixtures():
    p = _policies()
    parsed = p.parse_escalation_rules(RULES_MD)
    assert parsed["top"]["max_escalation_depth"] == 3
    rules = parsed["rules"]
    assert {r["id"] for r in rules} == {
        "paid_quote_over_ceiling",
        "contract_change_request",
        "off_whitelist_sku",
        "shipping_address_change",
        "content_brand_safety",
    }

    fixtures: list[tuple[str, list[str], str | None]] = [
        # 1. KOL quotes $1500, ceiling $800 → paid_over_ceiling
        ("compensation_overbudget",
         ["compensation.kol_quoted_over_ceiling"],
         "paid_quote_over_ceiling"),
        # 2. Pure "over budget" signal also matches the same rule (alias)
        ("compensation_alias",
         ["compensation.over_budget"],
         "paid_quote_over_ceiling"),
        # 3. KOL requests exclusivity change → contract_change_request
        ("contract_exclusivity_objection",
         ["contract.exclusivity_objection"],
         "contract_change_request"),
        # 4. KOL replies asking to modify clauses → contract_change_request
        ("contract_change_request_plain",
         ["contract.change_request"],
         "contract_change_request"),
        # 5. Off-whitelist SKU
        ("off_whitelist_sku",
         ["product.off_whitelist_request"],
         "off_whitelist_sku"),
        # 6. Shipping address change post-lock
        ("shipping_address_change",
         ["shipping.address_change_after_lock"],
         "shipping_address_change"),
        # 7. Brand-safety concern flagged by reviewer
        ("content_brand_safety",
         ["content.brand_safety_concern"],
         "content_brand_safety"),
        # 8. Disclosure missing also triggers brand-safety rule
        ("content_disclosure_missing",
         ["content.disclosure_missing"],
         "content_brand_safety"),
        # 9. Multi-signal — first matching rule by document order wins
        ("multi_signal_first_wins",
         ["compensation.over_budget", "content.brand_safety_concern"],
         "paid_quote_over_ceiling"),
        # 10. Benign confirmation reply → no rule
        ("benign_reply",
         ["interest.confirmed"],
         None),
        # 11. Empty signal list → no rule (cold-outreach acks etc.)
        ("empty_signals",
         [],
         None),
    ]

    for name, signals, expected in fixtures:
        got = _signal_match(rules, signals)
        assert got == expected, f"{name}: expected {expected!r} got {got!r}"


def test_classifier_handles_empty_rules_doc():
    p = _policies()
    parsed = p.parse_escalation_rules("")
    assert parsed == {"top": {}, "rules": []}
    # No rules means no rule can fire even on legitimate signals.
    assert _signal_match(parsed["rules"],
                         ["compensation.kol_quoted_over_ceiling"]) is None


def test_classifier_unknown_signal_does_not_match():
    p = _policies()
    rules = p.parse_escalation_rules(RULES_MD)["rules"]
    assert _signal_match(rules, ["weather.is_rainy"]) is None
