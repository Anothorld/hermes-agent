"""Step 7 — End-to-end regression tests.

Runs the full assembly chain ``_article_body → validate → fill_blog_template``
on both the inline and editorial fixtures. For editorial, also verifies the
``<a href=PDP><img></a>`` structure is well-formed and discoverable by a
standard HTML parser (the WP image-download path walks ``<img>`` tags, which
must remain reachable even when wrapped in an anchor).
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import re

import pytest

import server

_VALIDATE_CANDIDATES = [
    pathlib.Path.home() / ".hermes" / "skills" / "productivity" / "povison-seo-blog" / "scripts" / "validate-article.py",
    pathlib.Path("/Users/arnold/.hermes/skills/productivity/povison-seo-blog/scripts/validate-article.py"),
]
_VALIDATE_PATH = next((p for p in _VALIDATE_CANDIDATES if p.exists()), None)
_validate_mod = None
if _VALIDATE_PATH:
    _spec = importlib.util.spec_from_file_location("validate_article_mod", _VALIDATE_PATH)
    _validate_mod = importlib.util.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(_validate_mod)  # type: ignore


# ---------------------------------------------------------------------------
# t70 — E2E inline (full chain unchanged)
# ---------------------------------------------------------------------------
def test_t70_e2e_inline_chain(golden_inline):
    """golden_inline through the full assembly chain produces the same structural
    output as the Step 0 baseline; inline path is unaffected by the editorial
    feature end-to-end."""
    body_html = server._article_body(golden_inline)
    full_html = server.fill_blog_template(golden_inline)
    assert "<article" in full_html and "</article>" in full_html
    assert 'id="introduction"' in body_html
    assert 'id="povison-picks"' not in body_html  # editorial not active
    # validate runs without crashing; inline rules apply.
    if _validate_mod:
        vr = _validate_mod.validate(copy.deepcopy(golden_inline))
        assert vr["total"] == 13
        # products must be ok (2 accepted, inline 1-2 band).
        ids_ok = {c["id"]: bool(c["ok"]) for c in vr["checks"]}
        assert ids_ok["products"] is True


# ---------------------------------------------------------------------------
# t71 — E2E editorial (full chain renders povison-picks)
# ---------------------------------------------------------------------------
def test_t71_e2e_editorial_chain(golden_editorial):
    """golden_editorial through the full chain: _article_body inserts the
    POVISON Picks H2 before the Conclusion, fill_blog_template wraps it in the
    article template, and validate() applies editorial rules (products===3,
    links skipped, editorial_card_complete ok)."""
    body_html = server._article_body(golden_editorial)
    full_html = server.fill_blog_template(golden_editorial)
    # Editorial block present in body and template.
    assert 'id="povison-picks"' in body_html
    assert "POVISON Picks" in full_html
    # Editorial block sits before the Conclusion.
    assert body_html.find('id="povison-picks"') < body_html.find('<div class="conclusion">')
    # 3 H3 cards.
    h3s = re.findall(r"<h3[^>]*>", body_html)
    assert len(h3s) >= 3
    # validate() applies editorial rules.
    if _validate_mod:
        vr = _validate_mod.validate(copy.deepcopy(golden_editorial))
        ids_ok = {c["id"]: bool(c["ok"]) for c in vr["checks"]}
        assert ids_ok["products"] is True  # === 3
        assert ids_ok["links"] is True  # skipped
        assert ids_ok["editorial_card_complete"] is True


# ---------------------------------------------------------------------------
# t72 — WP compatibility (editorial image structure is parseable)
# ---------------------------------------------------------------------------
def test_t72_e2e_wp_compat_editorial_image_structure(golden_editorial):
    """The editorial card image is ``<a href=PDP><img ...></a>``. The WP image
    pipeline walks ``<img>`` tags (regardless of parent <a>); assert each
    editorial image tag is discoverable by regex and the surrounding <a>
    carries the PDP href (not the image src), so wp_publish will download the
    image to Media Library while preserving the PDP link."""
    body_html = server._article_body(golden_editorial)
    # Every editorial product image appears as <img ...> inside <a href=PDP>.
    for p in golden_editorial["products"]:
        if p.get("image"):
            # The <img> tag with the product image src is present.
            assert p["image"] in body_html
            # The PDP href is present as an <a href>.
            pdp = p["url"]
            assert f'href="{pdp}"' in body_html
    # Structural sanity: each <a href=PDP>...<img>... pairing — the <img> must
    # follow an <a> opening tag (the image is inside the anchor, not a sibling).
    # Find every PDP anchor and confirm an <img appears before the </a>.
    for m in re.finditer(r'<a\s+href="([^"]+povison\.com/products/[^"]+)"[^>]*>(.*?)</a>', body_html, re.S):
        inner = m.group(2)
        assert "<img" in inner, "editorial image must be INSIDE the PDP anchor, not a sibling"
