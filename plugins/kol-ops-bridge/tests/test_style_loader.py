"""Audit D1 — style-loader P0/P1/P2 composition coverage.

The runtime style loader (consumed by every outbound email skill via
``>>> include: kol-email-style-loader``) reads three layered policies
from ``policy_documents``:

* P0  ``company_style``           — global; required tone of voice.
* P1  ``user_style[owner_id]``    — per-operator overrides; optional.
* P2  per-campaign overrides      — out of scope here (lives in
                                    ``campaign_config`` future field).

These tests pin the contract that:
1. Both policies are versioned + append-only — only the latest active
   row is returned.
2. user_style is owner-scoped; absence of a per-user row falls back to
   ``None`` (not the company default — composition happens at the skill
   layer).
3. Empty / missing policies do not raise; loader callers can treat them
   as fall-throughs.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


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


def _compose(company: dict | None, user: dict | None) -> str:
    """Mirror the markdown stitch the skill performs (P0 then P1)."""
    parts: list[str] = []
    if company:
        parts.append("## Company voice (P0)\n" + company["content_md"].strip())
    if user:
        parts.append("## Operator overrides (P1)\n" + user["content_md"].strip())
    return "\n\n".join(parts)


def test_p0_only_when_user_missing(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        p.put_policy(
            conn,
            scope="company_style",
            content_md="Always sign with: POVISON Team.",
            updated_by="owner@console",
        )
        company = p.get_policy(conn, scope="company_style")
        user = p.get_policy(conn, scope="user_style", owner_user_id=42)
    assert company is not None
    assert user is None  # no row for owner 42
    composed = _compose(company, user)
    assert "Company voice (P0)" in composed
    assert "Operator overrides (P1)" not in composed
    assert "POVISON Team" in composed


def test_p1_overrides_after_p0(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        p.put_policy(
            conn,
            scope="company_style",
            content_md="Default tone: professional, concise.",
            updated_by="owner@console",
        )
        p.put_policy(
            conn,
            scope="user_style",
            owner_user_id=7,
            content_md="Use 'Cheers,' as my closer.",
            updated_by="alice",
        )
        company = p.get_policy(conn, scope="company_style")
        user = p.get_policy(conn, scope="user_style", owner_user_id=7)
    composed = _compose(company, user)
    # P0 appears first, P1 second.
    p0_idx = composed.index("Company voice (P0)")
    p1_idx = composed.index("Operator overrides (P1)")
    assert p0_idx < p1_idx
    assert "professional, concise" in composed
    assert "Cheers," in composed


def test_both_missing_yields_empty_string(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        company = p.get_policy(conn, scope="company_style")
        user = p.get_policy(conn, scope="user_style", owner_user_id=1)
    assert company is None and user is None
    assert _compose(company, user) == ""


def test_versioning_returns_latest_active(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        p.put_policy(
            conn,
            scope="company_style",
            content_md="v1 wording",
            updated_by="owner@console",
        )
        p.put_policy(
            conn,
            scope="company_style",
            content_md="v2 polished wording",
            updated_by="owner@console",
        )
        latest = p.get_policy(conn, scope="company_style")
        history = p.list_policy_history(conn, scope="company_style")
    assert latest is not None
    assert latest["version"] == 2
    assert "v2 polished" in latest["content_md"]
    assert [h["version"] for h in history] == [2, 1]
    assert [h["is_active"] for h in history] == [1, 0]


def test_user_style_isolated_per_owner(cal_db):
    p = _policies()
    with cal_db._connect() as conn:
        p.put_policy(
            conn, scope="user_style", owner_user_id=7,
            content_md="Alice closer", updated_by="alice",
        )
        p.put_policy(
            conn, scope="user_style", owner_user_id=8,
            content_md="Bob closer", updated_by="bob",
        )
        a = p.get_policy(conn, scope="user_style", owner_user_id=7)
        b = p.get_policy(conn, scope="user_style", owner_user_id=8)
        c = p.get_policy(conn, scope="user_style", owner_user_id=9)
    assert a and "Alice" in a["content_md"]
    assert b and "Bob" in b["content_md"]
    assert c is None  # third operator has no policy
