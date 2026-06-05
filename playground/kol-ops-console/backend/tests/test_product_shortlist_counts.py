"""Shortlist counts on product campaigns must include discovery-pool rows."""

from __future__ import annotations

from app.routers.products import _visible_shortlist_rows


def test_visible_shortlist_rows_includes_discovered_and_excludes_rejected():
    rows = [
        {"identity_id": 1, "candidate_status": "discovered"},
        {"identity_id": 2, "candidate_status": "shortlisted"},
        {"identity_id": 3, "candidate_status": "selected_for_outreach"},
        {"identity_id": 4, "candidate_status": "rejected"},
        {"identity_id": 5, "candidate_status": "archived"},
    ]
    visible = _visible_shortlist_rows(rows)
    assert {r["identity_id"] for r in visible} == {1, 2, 3}
