"""Unit tests for discovery_gate structured resume (pending_ingests)."""

from __future__ import annotations

import sqlite3

import pytest

from app.discovery_gate import (
    _collect_pending_ingests_for_resume,
    _compose_rediscover_brief,
    _extract_run_diagnostics,
    _render_resume_directives_block,
)


ROUND8_SAMPLE = """## Run Summary — Round 8 (rediscover)

### New candidates qualified: 1 (NOT yet persisted — JSON format issue blocked final ingest call)

**Qualified but unpersisted:**
- **techbymidas** — 346K followers, Canada-based

---

floor_unmet_reason: Only 1 new candidate qualified out of 8+ profiles visited.

attempted_angles:
  - dadrianca similar accounts cluster
  - Instagram #hometheater hashtag exploration
"""


def _product_row() -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE products (sku TEXT, name TEXT, url TEXT, tags_json TEXT, "
        "notes TEXT, pitch_md TEXT, selling_points TEXT, variants_json TEXT)"
    )
    conn.execute(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?)",
        ("SKU-1", "Test Product", "", "[]", "", "", "", "[]"),
    )
    conn.commit()
    return conn.execute("SELECT * FROM products").fetchone()


def test_extract_explicit_pending_ingests_yaml():
    text = """
pending_ingests:
  - "foo_handle — iteration limit before ingest"
  - "bar_handle — json validation failed"
attempted_angles:
  - "#hometheater"
"""
    diag = _extract_run_diagnostics(text)
    assert diag["pending_ingests"] is not None
    assert len(diag["pending_ingests"]) == 2
    assert "foo_handle" in diag["pending_ingests"][0]


def test_extract_pending_ingests_heuristic_round8():
    diag = _extract_run_diagnostics(ROUND8_SAMPLE)
    assert diag["pending_ingests"] is not None
    assert any("techbymidas" in item for item in diag["pending_ingests"])


def test_compose_brief_includes_resume_directives():
    product = _product_row()
    prior = [
        {
            "round_index": 8,
            "run_id": "run_old",
            "pending_ingests": [
                "techbymidas — qualified Round 8, not in CAL",
            ],
            "next_round_focus": [
                "@thesetupking — seed from blackprism bio",
            ],
        }
    ]
    brief = _compose_rediscover_brief(
        campaign_id="CID-1",
        env="LIVE",
        product=product,
        additional_count=3,
        excluded_handles=["other_handle"],
        test_mode_to=None,
        prior_diagnostics=prior,
    )
    assert "# resume_directives" in brief
    assert "STEP_0:" in brief
    assert "techbymidas" in brief
    assert "next_round_focus:" in brief
    assert "@thesetupking" in brief
    assert "0. If `# resume_directives` is present" in brief


def test_resume_directives_omit_handles_already_in_pool():
    product = _product_row()
    prior = [
        {
            "round_index": 2,
            "pending_ingests": ["techbymidas — still pending"],
        }
    ]
    brief = _compose_rediscover_brief(
        campaign_id="CID-1",
        env="LIVE",
        product=product,
        additional_count=1,
        excluded_handles=["techbymidas"],
        test_mode_to=None,
        prior_diagnostics=prior,
    )
    assert "# resume_directives (HARD" not in brief
    assert "pending_ingest_count:" not in brief


def test_collect_pending_ingests_dedup_latest_first():
    prior = [
        {"pending_ingests": ["old_handle — from round 1"]},
        {
            "pending_ingests": [
                "new_handle — round 2",
                "old_handle — duplicate",
            ]
        },
    ]
    out = _collect_pending_ingests_for_resume(prior, [])
    assert len(out) == 2
    assert out[0].startswith("new_handle")
    assert any("old_handle" in x for x in out)


def test_heuristic_does_not_false_positive_on_attempted_angles():
    text = """### New candidates qualified: 1 (NOT yet persisted)
attempted_angles:
  - dadrianca cluster
"""
    diag = _extract_run_diagnostics(text)
    assert diag.get("pending_ingests") is None


def test_render_resume_directives_block():
    lines = _render_resume_directives_block(["techbymidas — reason"])
    text = "\n".join(lines)
    assert "pending_ingest_count: 1" in text
    assert "ingest-confirmed-candidate" in text
