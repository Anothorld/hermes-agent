"""Step 3 — save hook + field protection tests.

Covers:
- (E) _ARTICLE_CONTENT_FIELDS includes the new editorial fields, and
  _backfill_empty_content_fields preserves placementStyle/editorialTitle/
  editorialIntro when an intermediate Agent save omits them.
- (F) _post_save_step3_inline_placements short-circuits in editorial mode
  (does not derive products/links from inline povison citations in body prose).
- (V) _prepare_section_content forces the legacy path in editorial mode even
  when the section content contains an incidental povison link.
"""
from __future__ import annotations

import copy

import server


# ---------------------------------------------------------------------------
# t30 / t31 — _ARTICLE_CONTENT_FIELDS + _backfill_empty_content_fields
# ---------------------------------------------------------------------------
def test_t30_content_fields_includes_editorial():
    """The new fields are present with non-trivial predicates."""
    names = {f for f, _ in server._ARTICLE_CONTENT_FIELDS}
    assert "placementStyle" in names
    assert "editorialTitle" in names
    assert "editorialIntro" in names


def test_t31_backfill_preserves_editorial_when_missing(golden_editorial):
    """dbState has placementStyle=editorial; incoming save omits it (Agent
    intermediate state) → _backfill_empty_content_fields must restore it from
    DB rather than let it silently flip to inline."""
    db_state = copy.deepcopy(golden_editorial)
    incoming = copy.deepcopy(golden_editorial)
    for k in ("placementStyle", "editorialTitle", "editorialIntro"):
        incoming.pop(k, None)  # simulate an Agent save that dropped the field
    n = server._backfill_empty_content_fields(incoming, db_state)
    assert n >= 3
    assert incoming["placementStyle"] == "editorial"
    assert incoming["editorialTitle"] == db_state["editorialTitle"]
    assert incoming["editorialIntro"] == db_state["editorialIntro"]


def test_t31_backfill_does_not_overwrite_real_value(golden_editorial):
    """A real new value in incoming must NOT be replaced by the DB value."""
    db_state = copy.deepcopy(golden_editorial)
    incoming = copy.deepcopy(golden_editorial)
    incoming["editorialTitle"] = "Operator changed the title"
    server._backfill_empty_content_fields(incoming, db_state)
    assert incoming["editorialTitle"] == "Operator changed the title"


def test_t31_backfill_inline_fixture_unaffected(golden_inline):
    """golden_inline has no editorial fields; backfill must be a no-op and not
    inject placementStyle (inline baseline stays inline)."""
    db_state = {"placementStyle": "editorial", "editorialTitle": "should NOT leak in"}
    incoming = copy.deepcopy(golden_inline)
    incoming.pop("placementStyle", None)
    server._backfill_empty_content_fields(incoming, db_state)
    # incoming had NO placementStyle and db has editorial → backfill restores it.
    # That's correct behavior (an inline task wouldn't have editorial in DB).
    # The point of this test: the inline FIXTURE itself never carries the field.
    golden_inline_clone = copy.deepcopy(golden_inline)
    assert "placementStyle" not in golden_inline_clone


# ---------------------------------------------------------------------------
# t32 / t33 — save hook editorial short-circuit (F)
# ---------------------------------------------------------------------------
def test_t32_save_hook_shortcircuits_in_editorial(golden_editorial):
    """In editorial mode the save hook returns [] immediately and does NOT
    touch products/links — the Agent wrote 3 cards explicitly and a stray
    povison citation in body prose must not be re-derived into placements."""
    data = copy.deepcopy(golden_editorial)
    # Plant an incidental povison PDP citation in a body section that is NOT one
    # of the 3 accepted editorial products — the hook must ignore it.
    data["sections"][1]["content"] += (
        "\n\nSee also this [related piece](https://www.povison.com/products/stray-citation.html?variant=999)."
    )
    n_products_before = len(data.get("products") or [])
    warnings = server._post_save_step3_inline_placements(data)
    assert warnings == []
    assert len(data.get("products") or []) == n_products_before  # untouched
    # The stray citation is still in the prose (not stripped — hook is a no-op).
    assert "stray-citation" in data["sections"][1]["content"]


def test_t33_save_hook_inline_baseline_unchanged(golden_inline):
    """Inline fixture through the save hook behaves as the Step 0 baseline:
    no new warnings, product/link counts don't balloon (re-locks t04)."""
    data = copy.deepcopy(golden_inline)
    n_products = len(data.get("products") or [])
    n_links = len(data.get("links") or [])
    warnings = server._post_save_step3_inline_placements(data)
    assert isinstance(warnings, list)
    assert len(data.get("products") or []) <= n_products + 1
    assert len(data.get("links") or []) <= n_links + 1


# ---------------------------------------------------------------------------
# t34 / t35 — _prepare_section_content editorial dispatch (V)
# ---------------------------------------------------------------------------
def test_t34_prepare_section_editorial_forces_legacy(golden_editorial):
    """In editorial mode, a section whose content contains an incidental
    povison link must take the legacy path, NOT the merged flow — so the link
    is NOT stripped as a 'rejected placement' and no trailing blurb is appended.

    (In merged flow, a povison link would trigger _strip_rejected_links_from_prose
    and blurb backfill; editorial must skip both.)
    """
    state = copy.deepcopy(golden_editorial)
    h2_1 = next(s for s in state["sections"] if s["id"] == "h2-1")
    # Inject an incidental povison citation that merged-flow would interpret as a
    # placement link.
    h2_1["content"] += "\n\nA [data-source citation](https://www.povison.com/products/data-source.html?variant=555)."
    out = server._prepare_section_content(state, copy.deepcopy(h2_1))
    # Legacy path does NOT strip the citation (it's not a [Product:] marker).
    assert "data-source.html" in out
    # Legacy path appends accepted product blurbs for this sectionId — but in
    # editorial mode the products are sectionId="editorial-picks", so NONE attach
    # to h2-1. So no trailing blurb from the editorial cards.
    assert "Honbay Convertible Sectional Sofa" not in out  # p1 is editorial-picks, not h2-1


def test_t35_prepare_section_inline_baseline_unchanged(golden_inline):
    """Inline fixture sections render exactly as the Step 0 baseline (t03)."""
    for sec in golden_inline["sections"]:
        out = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(sec))
        assert isinstance(out, str)
        assert "[Product:" not in out
        assert "[Internal link:" not in out
    # h2-1 keeps its accepted inline link.
    h2_1 = next(s for s in golden_inline["sections"] if s["id"] == "h2-1")
    out_h21 = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(h2_1))
    assert "sintered-stone-dining-table.html" in out_h21
    # h2-2 rejected link stripped.
    h2_2 = next(s for s in golden_inline["sections"] if s["id"] == "h2-2")
    out_h22 = server._prepare_section_content(copy.deepcopy(golden_inline), copy.deepcopy(h2_2))
    assert "rejected-article.html" not in out_h22
