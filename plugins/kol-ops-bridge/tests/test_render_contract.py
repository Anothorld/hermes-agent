"""Smoke tests for the contract renderer.

Verifies the three behaviors the kol-contract-coordinator skill relies on:
- every ${...} placeholder is replaced (no leakage into the signed doc),
- the FEE line is dropped iff no fee is supplied,
- the deliverable table reflects exactly the supplied rows (template
  sample rows do NOT leak through).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _PLUGIN_ROOT / "templates" / "povison_agreement.docx"
_RENDERER_PATH = _PLUGIN_ROOT / "scripts" / "render_contract.py"


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_contract_under_test", _RENDERER_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def renderer():
    pytest.importorskip("docx")
    return _load_renderer()


@pytest.fixture
def docx_module():
    return pytest.importorskip("docx")


def _all_paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _full_fields() -> dict:
    return {
        "date": "2026-05-22",
        "influencer": {
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1 415 555 0101",
            "address": "1 Market St, SF",
            "instagram": "https://instagram.com/janedoe",
            "tiktok": "https://tiktok.com/@janedoe",
            "youtube": "",
        },
        "product": {"specs": "SKU TS8319 - Sofa", "link": "https://povison.com/x"},
        "fee": {"amount": "150", "currency": "USD"},
        "deliverables": [
            {
                "type": "Reel",
                "description": "Product showcase",
                "quantity": "1 video",
                "requirements": "30s vertical",
                "time_of_uploading": "Within 2 weeks",
                "platform_of_uploading": "IG + TikTok",
            }
        ],
    }


def test_template_exists():
    assert _TEMPLATE.is_file(), f"template missing at {_TEMPLATE}"


def test_renders_with_fee(tmp_path, renderer, docx_module):
    out = tmp_path / "with_fee.docx"
    renderer.render(_TEMPLATE, out, _full_fields())
    assert out.is_file()
    doc = docx_module.Document(str(out))
    leftover = [p.text for p in _all_paragraphs(doc) if "${" in p.text]
    assert not leftover, f"unsubstituted placeholders: {leftover}"
    assert any("Jane Doe" in p.text for p in doc.paragraphs)
    assert any("150" in p.text and "flat fee" in p.text for p in doc.paragraphs)
    table = doc.tables[0]
    assert len(table.rows) == 2  # header + 1 deliverable
    assert "Reel" in table.rows[1].cells[0].text


def test_renders_without_fee_drops_flat_fee_line(tmp_path, renderer, docx_module):
    fields = _full_fields()
    fields["fee"] = None
    out = tmp_path / "no_fee.docx"
    renderer.render(_TEMPLATE, out, fields)
    doc = docx_module.Document(str(out))
    assert not any("flat fee" in p.text for p in doc.paragraphs)
    # surrounding section header and payment-terms line are intentionally kept
    assert any("Cash Compensation" in p.text for p in doc.paragraphs)


def test_multiple_deliverables_expand_table(tmp_path, renderer, docx_module):
    fields = _full_fields()
    fields["deliverables"] = [
        {
            "type": "Reel",
            "description": "A",
            "quantity": "1",
            "requirements": "r",
            "time_of_uploading": "t",
            "platform_of_uploading": "IG",
        },
        {
            "type": "Ad Codes",
            "description": "B",
            "quantity": "/",
            "requirements": "/",
            "time_of_uploading": "upon posting",
            "platform_of_uploading": "IG",
        },
        {
            "type": "BTS",
            "description": "C",
            "quantity": "5 photos",
            "requirements": "raw",
            "time_of_uploading": "day-of",
            "platform_of_uploading": "IG stories",
        },
    ]
    out = tmp_path / "multi.docx"
    renderer.render(_TEMPLATE, out, fields)
    doc = docx_module.Document(str(out))
    table = doc.tables[0]
    assert len(table.rows) == 4  # header + 3 deliverables
    assert table.rows[1].cells[0].text.strip() == "Reel"
    assert table.rows[2].cells[0].text.strip() == "Ad Codes"
    assert table.rows[3].cells[0].text.strip() == "BTS"


def test_missing_optional_fields_leave_label_intact(tmp_path, renderer, docx_module):
    fields = _full_fields()
    fields["influencer"]["youtube"] = ""
    fields["influencer"]["address"] = ""
    out = tmp_path / "partial.docx"
    renderer.render(_TEMPLATE, out, fields)
    doc = docx_module.Document(str(out))
    # The labels stay so the reader sees "YouTube: " / "Shipping Address: ";
    # the placeholder itself is gone.
    youtube_lines = [p.text for p in doc.paragraphs if p.text.startswith("YouTube")]
    assert youtube_lines
    assert "${" not in youtube_lines[0]


def test_cli_invocation(tmp_path, renderer):
    import json
    import subprocess

    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(_full_fields()), encoding="utf-8")
    out = tmp_path / "cli_out.docx"
    result = subprocess.run(
        [
            sys.executable,
            str(_RENDERER_PATH),
            "--template",
            str(_TEMPLATE),
            "--output",
            str(out),
            "--fields",
            str(fields_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    assert str(out) in result.stdout
