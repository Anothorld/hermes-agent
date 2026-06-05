"""Tests for legacy 曾触达 workbook touch index."""

from __future__ import annotations

from pathlib import Path


def test_touch_index_lookup_unions_rows_by_handle_and_email(bridge_pkg):
    pta = bridge_pkg.prior_touch_allowlist
    index = pta.TouchIndex(
        row_keys_by_identifier={
            "handle:kol_a": {"Sheet1!2", "Sheet2!5"},
            "email:kol_a@example.com": {"Sheet1!2", "Sheet3!9"},
        },
    )
    assert index.lookup_count(handle="kol_a", email="kol_a@example.com") == 3
    assert index.is_listed(handle="kol_a")


def test_default_workbook_includes_glowvia_lumiere(bridge_pkg):
    pta = bridge_pkg.prior_touch_allowlist
    pta.clear_touch_index_cache()
    assert pta.is_prior_touch_allowlisted(handle="glowvia_lumiere")
    assert not pta.is_prior_touch_allowlisted(handle="duchess.lifestyle")


def test_workbook_touch_count_at_least_one_for_listed_kol(bridge_pkg):
    pta = bridge_pkg.prior_touch_allowlist
    count = pta.get_internal_touch_count(handle="glowvia_lumiere")
    assert count >= 1


def test_get_internal_touch_count_zero_off_workbook(bridge_pkg):
    pta = bridge_pkg.prior_touch_allowlist
    assert pta.get_internal_touch_count(handle="duchess.lifestyle") == 0


def test_resolve_allowlist_prefers_live_xlsx_when_present(bridge_pkg, monkeypatch, tmp_path):
    pta = bridge_pkg.prior_touch_allowlist
    pta.clear_touch_index_cache()
    monkeypatch.delenv("KOL_PRIOR_TOUCH_ALLOWLIST_JSON", raising=False)
    monkeypatch.delenv("KOL_PRIOR_TOUCH_ALLOWLIST_XLSX", raising=False)
    live = tmp_path / "曾触达列表.xlsx"
    live.write_bytes(b"not-a-real-xlsx")
    monkeypatch.setattr(pta, "_DEFAULT_XLSX", live)
    assert pta.resolve_allowlist_path() == live


def test_parse_prior_touch_workbook_reads_all_sheets(bridge_pkg):
    pta = bridge_pkg.prior_touch_allowlist
    src = Path("/Users/arnold/Documents/曾触达列表.xlsx")
    if not src.exists():
        return
    index = pta.parse_prior_touch_workbook(src)
    assert "glowvia_lumiere" in index.handles
    assert index.lookup_count(handle="glowvia_lumiere") >= 1
    assert len(index.row_keys_by_identifier) >= 400
