"""Unit tests for discovery_gate structured resume (pending_ingests)."""

from __future__ import annotations

import sqlite3

import pytest

from app.discovery_gate import (
    EXIT_KIND_PREMATURE,
    PREMATURE_FLOOR_REASON,
    _collect_pending_ingests_for_resume,
    _compose_rediscover_brief,
    _count_consecutive_zero_new,
    _extract_run_diagnostics,
    _is_premature_bootstrap_stop,
    _render_resume_directives_block,
    _tag_premature_diagnostics,
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


def test_compose_rediscover_brief_caps_prior_runs_and_exclusion_sample():
    """Mature campaigns must not inject 100+ prior rounds into the brief."""
    product = _product_row()
    prior = []
    for i in range(1, 21):
        prior.append({
            "round_index": i,
            "run_id": f"run_{i}",
            "persisted_count_at_end": 500 + i,
            "target_floor": 613,
            "is_auto_retry": True,
            "attempted_angles": [f"angle-{i}-{j}" for j in range(30)],
            "visited_handles": [f"h{i}_{j} — DISCARD: x" for j in range(25)],
            "next_round_focus": [f"focus-{i}-{j}" for j in range(12)],
        })
    excluded = [f"kol_{i}" for i in range(80)]
    brief = _compose_rediscover_brief(
        campaign_id="CID-1",
        env="LIVE",
        product=product,
        additional_count=50,
        excluded_handles=excluded,
        test_mode_to=None,
        prior_diagnostics=prior,
    )
    assert "already_discovered_count: 80" in brief
    assert "already_discovered_handles_sample:" in brief
    assert "kol_0" in brief
    assert "kol_79" not in brief  # beyond sample cap
    assert "last 5 of 20 rounds" in brief
    assert "## Round 16 " in brief
    assert "## Round 20 " in brief
    assert "## Round 1 " not in brief
    assert "angle-20-0" in brief
    assert "angle-20-20" not in brief  # list item cap
    assert len(brief) < 40_000


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


def test_premature_empty_shell_r132_shape():
    """R132: bootstrap status text, no YAML diagnostics → premature."""
    text = (
        "Bootstrap completed.\n\n"
        "CAL candidate count: 590\n"
        "STEP_0 result: cationz is not present in the current CAL "
        "candidate-handle output and requires profile verification "
        "before any new discovery."
    )
    diag = _extract_run_diagnostics(text)
    assert _is_premature_bootstrap_stop(diag, text) is True


def test_premature_false_for_full_diagnostics_r131_shape():
    text = """
floor_unmet_reason: cationz already in CAL; new angles below 80K.
attempted_angles:
  - guest-room Google SERP
visited_handles:
  - "@houseofcomposition — DISCARD: 59 followers < 80K"
"""
    diag = _extract_run_diagnostics(text)
    assert _is_premature_bootstrap_stop(diag, text) is False


def test_premature_ignores_undecided_heuristic_visits():
    """Soft-stop prose with @handle must not clear premature via heuristic."""
    text = (
        "@cationz requires profile verification before any new discovery. "
        "pending verification."
    )
    diag = _extract_run_diagnostics(text)
    # Heuristic may record undecided visits; detector must still fire.
    assert diag.get("visited_handles")
    assert all(
        "undecided: heuristic" in str(item).lower()
        for item in (diag.get("visited_handles") or [])
    )
    assert _is_premature_bootstrap_stop(diag, text) is True


def test_premature_false_for_yaml_discard_visits():
    text = """
floor_unmet_reason: content mismatch after profile checks
attempted_angles:
  - next_round_focus queue
visited_handles:
  - "@simplyorganized — DISCARD: furniture_self_commerce_heuristic"
requires profile verification elsewhere in prose should not matter
"""
    diag = _extract_run_diagnostics(text)
    assert _is_premature_bootstrap_stop(diag, text) is False


def test_zero_new_streak_skips_premature_entries():
    history = [
        {"persisted_count_at_end": 3, "is_auto_retry": False},
        {"persisted_count_at_end": 3, "is_auto_retry": True},
        {
            "persisted_count_at_end": 3,
            "is_auto_retry": True,
            "exit_kind": EXIT_KIND_PREMATURE,
        },
        {
            "persisted_count_at_end": 3,
            "is_auto_retry": True,
            "exit_kind": EXIT_KIND_PREMATURE,
        },
    ]
    # Non-premature trailing pair is only the first two → streak 1.
    assert _count_consecutive_zero_new(history) == 1


def test_compose_brief_includes_premature_exit_recovery():
    product = _product_row()
    prior = [
        {
            "round_index": 132,
            "run_id": "run_fcbc",
            "persisted_count_at_end": 563,
            "target_floor": 613,
            "exit_kind": EXIT_KIND_PREMATURE,
            "floor_unmet_reason": PREMATURE_FLOOR_REASON,
        }
    ]
    brief = _compose_rediscover_brief(
        campaign_id="CID-1",
        env="LIVE",
        product=product,
        additional_count=50,
        excluded_handles=[],
        test_mode_to=None,
        prior_diagnostics=prior,
    )
    assert "# premature_exit_recovery (HARD)" in brief
    assert "rpa_precheck_handle" in brief
    assert "Illegal end" in brief


def test_tag_premature_diagnostics_sets_exit_kind():
    diag = {
        "floor_unmet_reason": None,
        "attempted_angles": None,
        "visited_handles": None,
    }
    _tag_premature_diagnostics(diag)
    assert diag["exit_kind"] == EXIT_KIND_PREMATURE
    assert diag["floor_unmet_reason"] == PREMATURE_FLOOR_REASON
