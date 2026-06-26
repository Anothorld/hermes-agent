"""Tests for draft_html normalization."""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from draft_html import normalize_draft_html, text_to_html  # noqa: E402


def test_plain_text_becomes_paragraphs():
    html = text_to_html("Hi there,\n\nThank you.")
    assert html == "<p>Hi there,</p><p>Thank you.</p>"


def test_single_newlines_become_br_within_paragraph():
    html = text_to_html("Line one\nLine two")
    assert html == "<p>Line one<br>Line two</p>"


def test_fake_html_body_wrapper_gets_paragraphs():
    raw = "<html><body>\nHi there,\n\nThank you.\n\n**Bold:**\n- item one\n- item two\n</body></html>"
    html = normalize_draft_html(raw)
    assert "<p>" in html
    assert "<br>" in html
    assert "<strong>Bold:</strong>" in html
    assert "<html>" not in html
    assert "item one" in html and "item two" in html


def test_real_paragraph_html_preserved():
    good = "<p>Hello</p><p>World</p>"
    assert normalize_draft_html(good) == good
