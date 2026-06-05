"""Tests for KOL registry XLSX export."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.kol_registry_export import (
    _audience_summary,
    registry_rows_for_sheet,
    rows_to_xlsx_bytes,
)


def test_rows_to_xlsx_bytes_is_valid_zip():
    data = rows_to_xlsx_bytes(
        ["序号", "ID"],
        [[1, "foo_handle"], [2, "bar"]],
    )
    assert data[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "xl/worksheets/sheet1.xml" in names
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "foo_handle" in sheet
        assert "序号" in sheet


def test_audience_summary_joins_facts():
    text = _audience_summary({
        "identity.nox_top_region": "US",
        "identity.nox_gender_skew": "女 62%",
    })
    assert "地区:US" in text
    assert "性别" in text


def test_registry_rows_for_sheet_maps_columns():
    rows = registry_rows_for_sheet([
        {
            "identity_id": 9,
            "handle": "@kol1",
            "ig_url": "https://www.instagram.com/kol1/",
            "internal_touch_count": 2,
            "target_spu": "SKU-1",
            "followers": 12000,
            "avg_views": 500,
            "email": "a@b.com",
            "has_initial_outreach_draft": True,
            "has_inbound_reply": False,
            "audience_facts": {},
        },
    ])
    assert rows[0][0] == 1
    assert rows[0][1] == "kol1"
    assert rows[0][3] == 2
    assert rows[0][4] == "SKU-1"
    assert rows[0][9] == "是"
    assert rows[0][10] == "否"
    assert len(rows[0]) == 11
