"""Phase E tests — policy_documents CRUD + escalation_rules parser."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _policies():
    key = "kol_ops_bridge_pkg.policies"
    if key in sys.modules:
        return sys.modules[key]
    # Trigger conftest's package loader by importing via cal fixture pattern:
    import types, importlib.util
    pkg_name = "kol_ops_bridge_pkg"
    plugin_root = Path(__file__).resolve().parents[1]
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(key, plugin_root / "policies.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def test_company_style_versioning(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        row1 = p.put_policy(
            conn,
            scope="company_style",
            content_md="Sign every email with: Best, POVISON Team.",
            updated_by="owner@console.app",
        )
        assert row1["version"] == 1
        assert row1["is_active"] == 1

        row2 = p.put_policy(
            conn,
            scope="company_style",
            content_md="(updated) Sign with: POVISON Team",
            updated_by="owner@console.app",
        )
        assert row2["version"] == 2
        assert row2["is_active"] == 1

        active = p.get_policy(conn, scope="company_style")
        assert active["id"] == row2["id"]
        assert active["version"] == 2

        history = p.list_policy_history(conn, scope="company_style")
        assert len(history) == 2
        assert [h["version"] for h in history] == [2, 1]
        assert [h["is_active"] for h in history] == [1, 0]


def test_user_style_per_owner(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        a = p.put_policy(
            conn,
            scope="user_style",
            owner_user_id=7,
            content_md="I prefer 'Cheers,' as my closer.",
            updated_by="alice",
        )
        b = p.put_policy(
            conn,
            scope="user_style",
            owner_user_id=8,
            content_md="I prefer 'Best regards,'.",
            updated_by="bob",
        )
        assert a["version"] == 1 and b["version"] == 1
        assert p.get_policy(conn, scope="user_style", owner_user_id=7)["content_md"].startswith(
            "I prefer 'Cheers,'"
        )
        assert p.get_policy(conn, scope="user_style", owner_user_id=8)["content_md"].startswith(
            "I prefer 'Best regards,'"
        )


def test_user_style_requires_owner(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        try:
            p.put_policy(
                conn,
                scope="user_style",
                content_md="x",
                updated_by="alice",
            )
        except ValueError as e:
            assert "user_style" in str(e)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


def test_company_style_rejects_owner(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        try:
            p.put_policy(
                conn,
                scope="company_style",
                owner_user_id=1,
                content_md="x",
                updated_by="someone",
            )
        except ValueError as e:
            assert "owner_user_id=NULL" in str(e)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


def test_escalation_rules_parser():
    p = _policies()
    md = """
max_escalation_depth: 5

### rule_id: paid_quote_over_ceiling
- signals_match: ["compensation.kol_quoted_over_ceiling"]
- severity: high
- suggested_question: "KOL quote exceeds paid_ceiling — approve?"
- required_facts_to_resume: ["paid_ceiling_override"]

### rule_id: contract_change_request
- signals_match: ["contract.change_request"]
- severity: normal
- suggested_question: "KOL wants to change exclusivity — accept?"
- required_facts_to_resume: []
"""
    out = p.parse_escalation_rules(md)
    assert out["top"]["max_escalation_depth"] == 5
    assert len(out["rules"]) == 2
    r = out["rules"][0]
    assert r["id"] == "paid_quote_over_ceiling"
    assert r["signals_match"] == ["compensation.kol_quoted_over_ceiling"]
    assert r["severity"] == "high"
    assert r["required_facts_to_resume"] == ["paid_ceiling_override"]


def test_escalation_rules_empty_returns_defaults():
    p = _policies()
    out = p.parse_escalation_rules("")
    assert out == {"top": {}, "rules": []}
