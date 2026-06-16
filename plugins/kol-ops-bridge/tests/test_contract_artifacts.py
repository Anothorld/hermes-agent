"""Tests for formal contract filenames and preview helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from kol_ops_bridge_pkg import contract_artifacts as ca


def test_build_contract_filename():
    name = ca.build_contract_filename(
        influencer_full_name="Megan McLeod",
        product_sku="SEB8008",
        when=__import__("datetime").date(2026, 6, 16),
    )
    assert name == "POVISON_Influencer_Agreement_Megan_McLeod_SEB8008_20260616.docx"


def test_sanitize_filename_part():
    assert ca.sanitize_filename_part("  Megan / McLeod  ") == "Megan_McLeod"


def test_resolve_contract_path_rejects_outside_root(tmp_path, monkeypatch):
    root = tmp_path / "contracts"
    root.mkdir()
    allowed = root / "LIVE" / "C1" / "ok.docx"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"x")
    monkeypatch.setenv("HERMES_KOL_OPS_CONTRACTS_DIR", str(root))
    assert ca.resolve_contract_path(allowed) == allowed.resolve()
    with pytest.raises(ValueError):
        ca.resolve_contract_path(tmp_path / "else.docx")


def test_contracts_root_follows_cal_db_env(tmp_path, monkeypatch):
    cal_db = tmp_path / "kol-ops-bridge" / "cal.db"
    cal_db.parent.mkdir(parents=True)
    cal_db.write_bytes(b"")
    monkeypatch.setenv("HERMES_KOL_OPS_CAL_DB", str(cal_db))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profiles" / "kol-orchestrator"))
    root = ca.contracts_root()
    assert root == (cal_db.parent / "contracts").resolve()


def test_ensure_formal_contract_path_renames_legacy(tmp_path, monkeypatch):
    root = tmp_path / "contracts"
    legacy = root / "LIVE" / "SEB8008-20260525" / "648_20260616.docx"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"fake")
    monkeypatch.setenv("HERMES_KOL_OPS_CONTRACTS_DIR", str(root))
    fields = {"influencer": {"full_name": "Megan McLeod"}, "product": {"specs": "SEB8008"}}
    out = ca.ensure_formal_contract_path(
        legacy,
        campaign_id="SEB8008-20260525",
        fields=fields,
    )
    assert out.name == "POVISON_Influencer_Agreement_Megan_McLeod_SEB8008_20260616.docx"
    assert not legacy.exists()
    assert out.is_file()


def test_docx_to_preview_html(bridge_pkg, tmp_path):
    from docx import Document

    path = tmp_path / "sample.docx"
    doc = Document()
    doc.add_paragraph("Hello contract")
    doc.save(str(path))
    html = ca.docx_to_preview_html(path)
    assert "Hello contract" in html
    assert "contract-docx-preview" in html
