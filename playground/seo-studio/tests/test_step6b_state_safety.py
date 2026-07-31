"""Step 6b — state-safety tests for the editorial section-step move (t61).

Covers the consensus critical fixes from the multi-model review:
  - editorial ``products`` are backfilled by ``_backfill_empty_content_fields``
    so an intermediate Agent save (products:[]) can't wipe the 3 cards the
    section step generated.
  - the placements prompt editorial branch no longer sets
    ``phaseDone.placements = true`` (re-pick semantics; cards stay 'pending').
  - ``links`` is intentionally NOT backfilled in editorial mode.
"""
from __future__ import annotations

import server


def test_t61_editorial_products_backfilled_when_empty():
    """Editorial mode: incoming products=[] but DB has 3 cards -> backfill keeps
    the 3 cards (an intermediate Agent save can't wipe the section step's
    output)."""
    db_cards = [{"name": "A", "url": "u1"}, {"name": "B", "url": "u2"},
                 {"name": "C", "url": "u3"}]
    data = {"placementStyle": "editorial", "products": []}
    n = server._backfill_empty_content_fields(data, {"products": db_cards})
    assert n >= 1
    assert data["products"] == db_cards


def test_t61_editorial_products_backfilled_when_missing():
    """Missing products field in editorial mode -> backfilled from DB."""
    db_cards = [{"name": "A"}]
    data = {"placementStyle": "editorial"}
    server._backfill_empty_content_fields(data, {"products": db_cards})
    assert data["products"] == db_cards


def test_t61_editorial_products_not_backfilled_when_present():
    """Non-empty incoming products in editorial mode -> NOT overwritten (the
    Agent's re-pick / operator's edit wins)."""
    incoming = [{"name": "X"}]
    data = {"placementStyle": "editorial", "products": incoming}
    server._backfill_empty_content_fields(data, {"products": [{"name": "DB"}]})
    assert data["products"] is incoming


def test_t61_inline_products_not_backfilled():
    """Inline mode: products is NOT in the protected set -- empty stays empty
    (the inline save hook re-derives products from prose; backfilling would
    restore deleted 404 recommendations)."""
    data = {"placementStyle": "inline", "products": []}
    server._backfill_empty_content_fields(data, {"products": [{"name": "DB"}]})
    assert data["products"] == []


def test_t61_editorial_links_not_backfilled():
    """Editorial mode: links is intentionally empty (no body internal links) --
    must NOT be backfilled even when DB has links."""
    data = {"placementStyle": "editorial", "links": []}
    server._backfill_empty_content_fields(data, {"links": [{"anchor": "a", "url": "u"}]})
    assert data["links"] == []


def test_t61_placements_prompt_editorial_no_phase_done_placements():
    """The placements prompt editorial branch must NOT instruct setting
    phaseDone.placements=true (re-pick semantics; cards stay 'pending' until the
    operator accepts them via confirmPlacements)."""
    g = server._PLACEMENTS_SUBSTEP_GUIDANCE
    assert "set articlestate.phaseDone.placements = true" not in g.lower()
    assert "re-pick" in g.lower()
