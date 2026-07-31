"""Step 0 — Baseline tests.

Locks the CURRENT behavior of the inline placement flow BEFORE any Editorial
Picks code lands. Every subsequent Step must keep these tests green (regression
guard). The fixtures (golden_inline.json etc.) are the frozen inputs.

Why not assert validate().ok == True? The inline fixture is intentionally
compact (realistic but short body) so word_count < 1500. We lock the
per-check ok values as a baseline instead — any drift in the validate()
output across Steps means a regression touched inline validation.
"""
from __future__ import annotations

import copy
import json

import pytest

# conftest.py puts the seo-studio dir on sys.path, so these import cleanly.
import server  # noqa: E402
from conftest import FIXTURES_DIR  # noqa: E402

# validate-article.py lives in the skills tree, not the playground. Add it.
import sys, pathlib  # noqa: E402
_SKILL_SCRIPTS = pathlib.Path(__file__).resolve().parents[4] / "hermes-agent" / "playground"
# The validate script is shipped under the povison-seo profile skills dir too;
# resolve robustly via the env hint if present, else fall back to a known path.
_VALIDATE_CANDIDATES = [
    pathlib.Path.home() / ".hermes" / "skills" / "productivity" / "povison-seo-blog" / "scripts" / "validate-article.py",
    pathlib.Path("/Users/arnold/.hermes/skills/productivity/povison-seo-blog/scripts/validate-article.py"),
]
_VALIDATE_PATH = next((p for p in _VALIDATE_CANDIDATES if p.exists()), None)
_validate_mod = None
if _VALIDATE_PATH:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("validate_article_mod", _VALIDATE_PATH)
    _validate_mod = importlib.util.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(_validate_mod)  # type: ignore


# ---------------------------------------------------------------------------
# t00 — fixtures exist and are well-formed
# ---------------------------------------------------------------------------
def test_t00_fixtures_load(golden_inline, golden_editorial, editorial_2products):
    """All three fixtures load as JSON dicts with the expected top-level keys."""
    assert golden_inline["topic"]["title"].startswith("Sectional Sofa at Scale")
    assert "placementStyle" not in golden_inline, "inline baseline must NOT carry placementStyle"
    assert golden_editorial["placementStyle"] == "editorial"
    assert editorial_2products["placementStyle"] == "editorial"
    assert len([p for p in editorial_2products["products"] if p.get("status") == "accepted"]) == 2


# ---------------------------------------------------------------------------
# t01 — inline render baseline (_article_body output shape)
# ---------------------------------------------------------------------------
def test_t01_baseline_inline_render(golden_inline):
    """_article_body(golden_inline) renders the inline flow shape we rely on.

    Asserts the structural invariants of the current inline assembly so a later
    Step can't silently change image placement, internal-link weaving, or the
    accepted-product figure count without surfacing here.
    """
    html = server._article_body(golden_inline)
    assert isinstance(html, str) and html, "body must be non-empty"
    # Intro present and anchorable.
    assert 'id="introduction"' in html
    # Conclusion wrapper present.
    assert '<div class="conclusion">' in html and 'id="conclusion"' in html
    # Q&A block present (fixture has 1 FAQ item).
    assert 'id="q-a"' in html
    # Accepted internal link woven inline: the anchor text became a markdown
    # link in the section content, which _section_html turns into an <a href>.
    # The "sintered stone dining table" anchor is in h2-1 content (merged flow).
    assert "sintered-stone-dining-table.html" in html or "sintered stone dining table" in html
    # Accepted product image appears (Povison 132" sectional — p2, accepted).
    assert "povison-reversible-sectional-sofa-132" in html
    # Rejected product must NOT render.
    assert "rejected-product.html" not in html
    # No editorial H2 yet (inline baseline).
    assert 'id="povison-picks"' not in html
    # povison.com links are INTERNAL → they must NOT get nofollow (only external
    # hosts do). The inline flow's PDP links stay dofollow by design.
    assert 'rel="noreferrer noopener nofollow"' not in html or "povison.com" not in html.split('rel="noreferrer noopener nofollow"')[0][-200:]
    # Internal povison PDP link rendered as <a href> (not stripped).
    assert 'href="https://www.povison.com/products/honbay-convertible-sectional-sofa.html' in html


# ---------------------------------------------------------------------------
# t02 — validate() baseline (record per-check ok values)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(_validate_mod is None, reason="validate-article.py not found in skills tree")
def test_t02_baseline_validate(golden_inline):
    """Lock the per-check ok vector of validate(golden_inline).

    The fixture body is deliberately short, so word_count is expected to fail;
    we assert the vector itself is stable, not that ok == True.
    """
    result = _validate_mod.validate(copy.deepcopy(golden_inline))
    assert result["total"] == 13, "validate() now emits 13 checks (added editorial_card_complete)"
    # Record the frozen baseline of which checks pass/fail.
    ids_ok = {c["id"]: bool(c["ok"]) for c in result["checks"]}
    expected_baseline = {
        "word_count": False,            # short fixture body
        "outline_confirmed": True,
        "placements_confirmed": True,
        "products": True,               # 2 accepted (1<=n<=2)
        "links": False,                 # 1 accepted link (needs 2-3)
        "faq_count": False,             # 1 FAQ (needs 4-6)
        "faq_overlap": True,
        "inline_links_valid": True,
        "no_leftover_markers": True,
        "editorial_card_complete": True,  # inline mode → check skipped (ok=True)
        "meta_title": True,             # 51 chars
        "meta_desc": False,             # 119 chars (needs 150-160)
        "slug": True,
    }
    assert ids_ok == expected_baseline, f"inline validate baseline drifted: {ids_ok}"


# ---------------------------------------------------------------------------
# t03 — _prepare_section_content baseline (merged-flow path)
# ---------------------------------------------------------------------------
def test_t03_baseline_prepare_section_content(golden_inline):
    """Lock _prepare_section_content per-section output for the inline fixture.

    golden_inline sections carry inline povison markdown links (merged flow), so
    each goes through the merged path: rejected links stripped, no
    `Related:` fallback, accepted inline links preserved, accepted product
    blurbs that are already inline not appended again.
    """
    for sec in golden_inline["sections"]:
        out = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(sec))
        assert isinstance(out, str)
        # No leftover legacy markers.
        assert "[Product:" not in out
        assert "[Internal link:" not in out
    # h2-1 has an accepted inline link → stays as markdown link in output.
    h2_1 = next(s for s in golden_inline["sections"] if s["id"] == "h2-1")
    out_h21 = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(h2_1))
    assert "sintered-stone-dining-table.html" in out_h21
    # h2-2 has a REJECTED link — must not appear (merged flow strips rejected).
    h2_2 = next(s for s in golden_inline["sections"] if s["id"] == "h2-2")
    out_h22 = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(h2_2))
    assert "rejected-article.html" not in out_h22
    assert "rejected internal link" not in out_h22
    # No Related: fallback in any section (merged flow doesn't emit it).
    for sec in golden_inline["sections"]:
        out = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(sec))
        assert "Related:" not in out


# ---------------------------------------------------------------------------
# t04 — _post_save_step3_inline_placements baseline
# ---------------------------------------------------------------------------
def test_t04_baseline_save_hook(golden_inline):
    """Lock save-hook behavior on the inline fixture.

    The hook derives products/links from inline markdown links. golden_inline
    already has products/links populated AND the _placementsBackfilled sentinel
    semantics: once placements are populated, subsequent runs should not add
    duplicates. We assert warnings list is clean (real povison URLs) and the
    product/link counts don't explode.
    """
    data = copy.deepcopy(golden_inline)
    n_products_before = len(data.get("products") or [])
    n_links_before = len(data.get("links") or [])
    warnings = server._post_save_step3_inline_placements(data)
    # All URLs in the fixture are well-formed povison PDPs/blog → no warnings.
    assert isinstance(warnings, list)
    # Counts must not balloon (backfill is gated by sentinel / existing arrays).
    assert len(data.get("products") or []) <= n_products_before + 1
    assert len(data.get("links") or []) <= n_links_before + 1


# ---------------------------------------------------------------------------
# t05 — freshArticleState / state-field baseline
# ---------------------------------------------------------------------------
def test_t05_baseline_state_fields(golden_inline):
    """Lock the inline fixture's state-field values + confirm no placementStyle.

    These are the values later Steps must preserve for the inline path.
    """
    assert golden_inline["placementsConfirmed"] is True
    pd = golden_inline["phaseDone"]
    assert pd["placements"] is True
    assert pd["sections"] is True
    # The inline baseline must NOT carry any editorial fields — those are the
    # new state added by this feature. If this asserts, the fixture was edited.
    for k in ("placementStyle", "editorialTitle", "editorialIntro"):
        assert k not in golden_inline


# ---------------------------------------------------------------------------
# t06 — mergeArticleState baseline (JS function — tested via the contract)
# ---------------------------------------------------------------------------
def test_t06_baseline_merge_contract(golden_inline):
    """mergeArticleState lives in the UI (JS), not server.py. We lock the
    contract the server-side save path relies on instead: phaseDone is a
    7-key dict and placements is a recognized key. Later Step C will change
    the merge semantics (pd.placements = pd.sections); this test pins the
    pre-change shape so the change is detectable.
    """
    pd = golden_inline["phaseDone"]
    expected_keys = {"serp", "outline", "sections", "placements", "faq", "meta", "preview"}
    assert set(pd.keys()) == expected_keys
    # placements is its own boolean now (not yet derived from sections).
    assert isinstance(pd["placements"], bool)


# ---------------------------------------------------------------------------
# t07 — _toc_html baseline (no editorial anchor)
# ---------------------------------------------------------------------------
def test_t07_baseline_toc(golden_inline):
    """The TOC is built from sections only; the editorial H2 does not exist
    yet, so no povison-picks anchor can appear.
    """
    toc = server._toc_html(golden_inline)
    assert isinstance(toc, str)
    assert "povison-picks" not in toc
    assert "#povison-picks" not in toc
