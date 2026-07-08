"""Tests for precheck — exclusion_set / skip / cooldown precheck."""

from __future__ import annotations

import precheck


def test_clean_handle_passes():
    r = precheck.precheck_handle("cleanuser")
    assert r["hard_discard"] == False
    assert r["gates"]["exclusion_precheck"]["pass"] == True


def test_skip_list_blocks():
    r = precheck.precheck_handle("blocked", skip_handles=["blocked"])
    assert r["hard_discard"] == True
    assert "skip_list_active" in r["discard_reasons"]


def test_cooldown_blocks():
    r = precheck.precheck_handle("cooled", cooldown_handles=["cooled"])
    assert r["hard_discard"] == True
    assert "outreach_cooldown_active" in r["discard_reasons"]


def test_exclusion_set_blocks():
    r = precheck.precheck_handle("pooled", exclusion_handles=["pooled"])
    assert r["hard_discard"] == True
    assert "already_in_pool" in r["discard_reasons"]


def test_multiple_reasons():
    r = precheck.precheck_handle(
        "multi",
        skip_handles=["multi"],
        cooldown_handles=["multi"],
    )
    assert r["hard_discard"] == True
    assert "skip_list_active" in r["discard_reasons"]
    assert "outreach_cooldown_active" in r["discard_reasons"]


def test_handle_with_at_prefix():
    r = precheck.precheck_handle("@blocked", skip_handles=["blocked"])
    assert r["hard_discard"] == True


def test_handle_case_insensitive():
    r = precheck.precheck_handle("Blocked", skip_handles=["blocked"])
    assert r["hard_discard"] == True


def test_candidate_status_map():
    r = precheck.precheck_handle(
        "pooled",
        exclusion_handles=["pooled"],
        candidate_status_map={"pooled": "rejected"},
    )
    assert r["gates"]["exclusion_precheck"]["value"] == "rejected"
