"""Step 6 — section sub-step guidance branches on placementStyle (t60).

The operator now picks a placement style in the 正文生成 phase BEFORE writing
sections, so the section Agent must branch its working mode on that choice:
  - inline: weave inline links into prose (merged-flow).
  - editorial: write prose WITHOUT inline links, then generate 3 review cards
    + editorialTitle/editorialIntro. Must NOT set phaseDone.placements.

These tests assert the two module-level constants exist and that
``_section_guidance_for_task`` picks the right branch from the materialized
article-state.json, without spinning up the agent-run endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

import server


def test_t60_section_guidance_constants_exist():
    assert isinstance(server._SECTION_SUBSTEP_GUIDANCE_INLINE, str)
    assert isinstance(server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL, str)
    # Inline branch mentions merged placements / inline weaving.
    inline_low = server._SECTION_SUBSTEP_GUIDANCE_INLINE.lower()
    assert "merged placements" in inline_low
    assert "inline" in inline_low
    # Editorial branch mentions 3 cards, no inline links, editorialTitle/intro.
    edit_low = server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL.lower()
    assert "editorial picks" in edit_low
    assert "without" in edit_low and "inline" in edit_low
    assert "3" in server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL
    assert "editorialtitle" in edit_low
    assert "editorialintro" in edit_low


def test_t60_section_guidance_editorial_does_not_set_phase_done_placements():
    """Critical: the editorial section guidance must NOT instruct the Agent to
    set phaseDone.placements=true (cards are 'pending'; confirmPlacements owns
    that flag). Setting it would skip the placements review panel."""
    g = server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL
    assert "phaseDone.placements = true" not in g
    assert "phaseDone.placements=true" not in g
    # It should explicitly tell the Agent NOT to set it.
    assert "do not set articlestate.phasedone.placements" in g.lower()


def test_t60_section_guidance_editorial_does_not_overwrite_title():
    """The Agent must only fill editorialTitle/editorialIntro when empty, not
    overwrite an operator-entered value."""
    g = server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL.lower()
    assert "only if it is currently empty" in g
    assert "do not overwrite" in g


def test_t60_section_guidance_for_task_inline_default(tmp_path):
    """No article-state.json → defaults to inline guidance."""
    d = tmp_path / "task-xyz"
    d.mkdir()
    chosen = server._section_guidance_for_task(d)
    assert chosen is server._SECTION_SUBSTEP_GUIDANCE_INLINE


def test_t60_section_guidance_for_task_inline_when_inline(tmp_path):
    d = tmp_path / "task-inline"
    d.mkdir()
    (d / "article-state.json").write_text(
        json.dumps({"placementStyle": "inline"}), encoding="utf-8"
    )
    assert server._section_guidance_for_task(d) is server._SECTION_SUBSTEP_GUIDANCE_INLINE


def test_t60_section_guidance_for_task_editorial_when_editorial(tmp_path):
    d = tmp_path / "task-edit"
    d.mkdir()
    (d / "article-state.json").write_text(
        json.dumps({"placementStyle": "editorial"}), encoding="utf-8"
    )
    assert server._section_guidance_for_task(d) is server._SECTION_SUBSTEP_GUIDANCE_EDITORIAL


def test_t60_section_guidance_for_task_missing_field_defaults_inline(tmp_path):
    d = tmp_path / "task-nostyle"
    d.mkdir()
    (d / "article-state.json").write_text(
        json.dumps({"topic": {"title": "x"}}), encoding="utf-8"
    )
    assert server._section_guidance_for_task(d) is server._SECTION_SUBSTEP_GUIDANCE_INLINE


def test_t60_section_guidance_for_task_corrupt_json_defaults_inline(tmp_path):
    d = tmp_path / "task-corrupt"
    d.mkdir()
    (d / "article-state.json").write_text("{not json", encoding="utf-8")
    # Must not raise — falls back to inline.
    assert server._section_guidance_for_task(d) is server._SECTION_SUBSTEP_GUIDANCE_INLINE
