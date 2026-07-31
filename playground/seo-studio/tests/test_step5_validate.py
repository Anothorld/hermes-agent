"""Step 5 — validate-article.py editorial-mode adaptation tests.

Validates that validate() reads placementStyle and applies editorial rules:
- products must be exactly 3 (not 1-2)
- links check is skipped (editorial carries no internal links)
- new editorial_card_complete check
- inline fixture still validates against the Step 0 baseline (regression)
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib

import pytest

_VALIDATE_CANDIDATES = [
    pathlib.Path.home() / ".hermes" / "skills" / "productivity" / "povison-seo-blog" / "scripts" / "validate-article.py",
    pathlib.Path("/Users/arnold/.hermes/skills/productivity/povison-seo-blog/scripts/validate-article.py"),
]
_VALIDATE_PATH = next((p for p in _VALIDATE_CANDIDATES if p.exists()), None)

if _VALIDATE_PATH:
    _spec = importlib.util.spec_from_file_location("validate_article_mod", _VALIDATE_PATH)
    _validate_mod = importlib.util.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(_validate_mod)  # type: ignore
else:
    _validate_mod = None  # type: ignore

pytestmark = pytest.mark.skipif(_validate_mod is None, reason="validate-article.py not found")


# ---------------------------------------------------------------------------
# t50 — editorial mode validation rules
# ---------------------------------------------------------------------------
def test_t50_editorial_3_products_passes(golden_editorial):
    """Editorial fixture with exactly 3 accepted products → products check ok,
    links check ok (skipped), editorial_card_complete ok."""
    result = _validate_mod.validate(copy.deepcopy(golden_editorial))
    ids_ok = {c["id"]: bool(c["ok"]) for c in result["checks"]}
    assert ids_ok["products"] is True  # === 3
    assert ids_ok["links"] is True  # skipped in editorial
    assert ids_ok["editorial_card_complete"] is True
    assert ids_ok["inline_links_valid"] is True  # skipped in editorial


def test_t50_editorial_2_products_fails(editorial_2products):
    """Editorial with only 2 products → products ok=False + editorial_card_complete ok=False."""
    result = _validate_mod.validate(copy.deepcopy(editorial_2products))
    ids_ok = {c["id"]: bool(c["ok"]) for c in result["checks"]}
    assert ids_ok["products"] is False  # not === 3
    assert ids_ok["editorial_card_complete"] is False


def test_t50_editorial_card_complete_flags_missing_fields(golden_editorial):
    """A card missing blurb/image/url → editorial_card_complete fails with a hint."""
    state = copy.deepcopy(golden_editorial)
    state["products"][0]["blurb"] = ""
    state["products"][1]["image"] = ""
    result = _validate_mod.validate(state)
    ec = next(c for c in result["checks"] if c["id"] == "editorial_card_complete")
    assert ec["ok"] is False
    assert "缺文案" in ec["label"]
    assert "缺图片" in ec["label"]


# ---------------------------------------------------------------------------
# t51 — inline regression (must match the Step 0 baseline vector)
# ---------------------------------------------------------------------------
def test_t51_inline_baseline_unchanged(golden_inline):
    """The inline fixture must produce the SAME per-check ok vector locked in
    Step 0 t02 — the editorial adaptation must not change inline behavior."""
    result = _validate_mod.validate(copy.deepcopy(golden_inline))
    ids_ok = {c["id"]: bool(c["ok"]) for c in result["checks"]}
    expected_baseline = {
        "word_count": False,
        "outline_confirmed": True,
        "placements_confirmed": True,
        "products": True,
        "links": False,
        "faq_count": False,
        "faq_overlap": True,
        "inline_links_valid": True,
        "no_leftover_markers": True,
        "editorial_card_complete": True,  # inline → skipped → ok=True
        "meta_title": True,
        "meta_desc": False,
        "slug": True,
    }
    assert ids_ok == expected_baseline, f"inline validate baseline drifted: {ids_ok}"
    assert result["total"] == 13
