"""Tests for Gate B contact param resolution."""

from __future__ import annotations

from app.nox_contacts_sync import (
    classify_nox_contacts_cli_failure,
    gate_b_eligible,
    resolve_nox_contacts_params,
)
from app.nox_quota import quota_exhausted_from_stats


def test_resolve_nox_contacts_from_creator_id() -> None:
    ident = {"platform": "youtube", "primary_handle": "foo"}
    facts = {"facts": {"identity.nox_creator_id": "nox-99"}}
    params = resolve_nox_contacts_params(ident, facts)
    assert params["nox_creator_id"] == "nox-99"
    assert gate_b_eligible(params)


def test_resolve_nox_contacts_from_platform_url() -> None:
    ident = {"platform": "tiktok"}
    facts = {
        "facts": {
            "identity.tiktok_profile_url": "https://www.tiktok.com/@kol",
        },
    }
    params = resolve_nox_contacts_params(ident, facts)
    assert params["url"] == "https://www.tiktok.com/@kol"
    assert params["platform"] == "tiktok"
    assert gate_b_eligible(params)


def test_gate_b_ineligible_without_handles() -> None:
    params = resolve_nox_contacts_params({"platform": "instagram"}, {"facts": {}})
    assert not gate_b_eligible(params)


def test_quota_exhausted_from_stats() -> None:
    assert quota_exhausted_from_stats({"usage": {"remaining_estimate": 0}})
    assert not quota_exhausted_from_stats({"usage": {"remaining_estimate": 3}})
    assert quota_exhausted_from_stats({"quota_exhausted": True})


def test_classify_nox_contacts_cli_failure_upstream_40017() -> None:
    out = {
        "success": False,
        "error_code": "NOX_CLI_ERROR",
        "detail": 'noxinfluencer exit 8: {"summary":"SaaS 40017: 配额不足"}',
    }
    result = classify_nox_contacts_cli_failure(out)
    assert result["reason"] == "nox_upstream_error"
    assert result.get("upstream_code") == "40017"
    assert "quota_exhausted" not in result


def test_classify_nox_contacts_cli_failure_saas_quota() -> None:
    out = {"success": False, "error_code": "NOX_QUOTA_EXCEEDED", "detail": "budget"}
    result = classify_nox_contacts_cli_failure(out)
    assert result["reason"] == "nox_saas_quota_exhausted"
    assert result["quota_exhausted"] is True


def test_classify_nox_contacts_cli_failure_generic() -> None:
    out = {"success": False, "error_code": "NOX_CLI_ERROR", "detail": "timeout"}
    result = classify_nox_contacts_cli_failure(out)
    assert result["reason"] == "contacts_cli_failed"
    assert "quota_exhausted" not in result
