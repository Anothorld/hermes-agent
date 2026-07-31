"""Step 2 — Editorial Picks rendering tests.

Pure-function tests against ``_editorial_picks_html`` / ``_editorial_card_html``
and the ``_article_body`` editorial branch. No FastAPI, no DB.
"""
from __future__ import annotations

import copy
import re

import server


# ---------------------------------------------------------------------------
# t21 — _editorial_picks_html structure
# ---------------------------------------------------------------------------
def test_t21_editorial_html_structure(golden_editorial):
    html = server._editorial_picks_html(golden_editorial)
    assert html, "editorial fixture (3 accepted products) must render non-empty"
    # H2 with the editorial id + the custom editorialTitle.
    assert '<h2 id="povison-picks">' in html
    assert "POVISON Picks — Best Sectional Sofas for Scale-Conscious Rooms" in html
    # editorialIntro paragraph.
    assert 'class="editorial-intro"' in html
    # Exactly 3 H3 headings (one per accepted product), no links inside the H3.
    h3s = re.findall(r"<h3[^>]*>(.*?)</h3>", html, re.S)
    assert len(h3s) == 3
    for h3_inner in h3s:
        assert "<a " not in h3_inner, "H3 heading must be plain text, no link"
    # Each product name appears as an H3 (the " is HTML-escaped to &quot; in the
    # heading text).
    assert "Honbay Convertible Sectional Sofa" in html
    assert 'Povison 132&quot; Reversible Sectional Sofa' in html
    assert "Povison 96&quot; Modular Loveseat Sectional" in html
    # Each product image wraps to its PDP (link_url).
    assert 'href="https://www.povison.com/products/honbay-convertible-sectional-sofa.html?variant=12345"' in html
    assert 'href="https://www.povison.com/products/povison-reversible-sectional-sofa-132.html?variant=67890"' in html
    # Blurb copy is present.
    assert "fully assembled" in html
    # p1 + p2 have reviewQuote → blockquote; p3 does not.
    assert html.count("<blockquote") == 2
    assert "Megan" in html and "October 17, 2025" in html
    assert "Colin" in html and "January 23, 2025" in html


def test_t21_editorial_title_falls_back_to_topic(golden_editorial):
    """When editorialTitle is empty, the H2 uses 'POVISON Picks — {topic.title}'."""
    state = copy.deepcopy(golden_editorial)
    state["editorialTitle"] = ""
    html = server._editorial_picks_html(state)
    topic_title = state["topic"]["title"]
    assert f"POVISON Picks — {topic_title}" in html


# ---------------------------------------------------------------------------
# t22 — degradation
# ---------------------------------------------------------------------------
def test_t22_degrade_when_inline(golden_inline):
    """inline mode → _editorial_picks_html returns '' (no editorial block)."""
    assert server._editorial_picks_html(golden_inline) == ""


def test_t22_degrade_when_not_editorial_style(golden_editorial):
    """placementStyle != 'editorial' → empty string even with 3 products."""
    state = copy.deepcopy(golden_editorial)
    state["placementStyle"] = "inline"
    assert server._editorial_picks_html(state) == ""


def test_t22_degrade_when_fewer_than_3_products(golden_editorial):
    """Fewer than 3 accepted products → empty string (fall back to inline)."""
    state = copy.deepcopy(golden_editorial)
    state["products"] = state["products"][:2]
    assert server._editorial_picks_html(state) == ""


def test_t22_degrade_when_no_products(golden_editorial):
    state = copy.deepcopy(golden_editorial)
    state["products"] = []
    assert server._editorial_picks_html(state) == ""


# ---------------------------------------------------------------------------
# t23 — _article_body editorial branch
# ---------------------------------------------------------------------------
def test_t23_article_body_editorial_inserts_picks_before_conclusion(golden_editorial):
    """In editorial mode, the POVISON Picks block appears BEFORE the Conclusion
    div, and per-section product images are suppressed (products live in the
    picks block)."""
    html = server._article_body(golden_editorial)
    # Editorial block present.
    assert 'id="povison-picks"' in html
    # Picks block appears before the conclusion div.
    picks_idx = html.find('id="povison-picks"')
    concl_idx = html.find('<div class="conclusion">')
    assert picks_idx != -1 and concl_idx != -1
    assert picks_idx < concl_idx
    # PDP image link is present (editorial card image → PDP).
    assert 'href="https://www.povison.com/products/honbay-convertible-sectional-sofa.html?variant=12345"' in html


# ---------------------------------------------------------------------------
# t24 — inline regression (inline path unchanged by the new branch)
# ---------------------------------------------------------------------------
def test_t24_inline_render_unchaged(golden_inline):
    """golden_inline (no placementStyle) must render exactly as the Step 0
    baseline — the editorial branch is a no-op in inline mode."""
    html = server._article_body(golden_inline)
    assert 'id="povison-picks"' not in html
    assert 'class="editorial-intro"' not in html
    # Inline product image still renders (not suppressed).
    assert "povison-reversible-sectional-sofa-132" in html
    # Same structural anchors as t01.
    assert 'id="introduction"' in html
    assert '<div class="conclusion">' in html
    assert 'id="q-a"' in html
