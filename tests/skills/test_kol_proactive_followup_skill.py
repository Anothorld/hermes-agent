"""Structure checks for kol-proactive-followup skill."""

from __future__ import annotations

import re
from pathlib import Path

_SKILL = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "social-media"
    / "kol-proactive-followup"
    / "SKILL.md"
)

_REQUIRED_SECTIONS = (
    "## When to Use",
    "## Prerequisites",
    "## Procedure",
    "## Examples",
    "## Pitfalls",
    "## Verification",
)


def test_skill_file_exists():
    assert _SKILL.is_file()


def test_frontmatter_description_length():
    text = _SKILL.read_text(encoding="utf-8")
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    assert m, "missing description"
    desc = m.group(1).strip()
    assert len(desc) <= 60, f"description too long ({len(desc)}): {desc}"


def test_required_sections_present():
    text = _SKILL.read_text(encoding="utf-8")
    for heading in _REQUIRED_SECTIONS:
        assert heading in text, f"missing section {heading}"


def test_documents_proactive_followup_kind():
    text = _SKILL.read_text(encoding="utf-8")
    assert "proactive_followup" in text
    assert "persist-reply-draft" in text
    assert "operator_topic" in text
