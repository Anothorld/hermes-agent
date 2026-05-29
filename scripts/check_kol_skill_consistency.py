#!/usr/bin/env python3
"""Lightweight consistency checks for KOL skill docs.

This script validates that KOL skills keep the shared-block structure added in
the optimization phases, so future edits don't drift back into duplicated
contracts.
"""

from __future__ import annotations

import argparse
from pathlib import Path


PHASE1_SKILLS = {
    "kol-cold-outreach",
    "kol-reengagement-outreach",
}

PHASE2_SKILLS = {
    "kol-interest-qualifier",
    "kol-product-selector",
    "kol-compensation-negotiator",
    "kol-shipping-intake",
    "kol-logistics-tracker",
    "kol-contract-coordinator",
    "kol-content-reviewer",
    "kol-deliverables-clarifier",
    "kol-brief-sender",
    "kol-golive-and-boost",
    "kol-payout-method-intake",
}

PHASE3_SKILLS = {
    "kol-reply-dispatcher",
    "kol-discovery-to-outreach-router",
    "kol-outreach-orchestrator-flow",
}

STRUCTURE_CHECK_SKILLS = (PHASE1_SKILLS | PHASE2_SKILLS | PHASE3_SKILLS) - {
    "kol-outreach-orchestrator-flow"
}

SPECIAL_NO_SECTION_REQUIREMENTS = {
    # Documentation-only map skill.
    "kol-outreach-orchestrator-flow",
}

SHARED_FILES = {
    "skills/social-media/kol/_shared/runtime-draft-guardrails.md",
    "skills/social-media/kol/_shared/style-and-brief-preambles.md",
    "skills/social-media/kol/_shared/greeting-name-resolution.md",
    "skills/social-media/kol/_shared/personalization-check.md",
    "skills/social-media/kol/_shared/reply-envelope-contract.md",
    "skills/social-media/kol/_shared/bridge-runtime-core.md",
    "skills/social-media/kol/_shared/router-dispatcher-boundaries.md",
}

LOCAL_SHARED_PREFIX = "references/shared/"


def _contains(text: str, needle: str) -> bool:
    return needle in text


def _check_file(path: Path, repo_root: Path) -> list[str]:
    rel = path.relative_to(repo_root).as_posix()
    name = path.parent.name
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []

    if (
        name in STRUCTURE_CHECK_SKILLS
        and not _contains(text, "## Goal")
        and name not in SPECIAL_NO_SECTION_REQUIREMENTS
    ):
        errors.append(f"{rel}: missing `## Goal`")

    if name in STRUCTURE_CHECK_SKILLS and name not in SPECIAL_NO_SECTION_REQUIREMENTS:
        for header in ("## Runtime Contract", "## Inputs", "## Procedure", "## Pitfalls"):
            if not _contains(text, header):
                errors.append(f"{rel}: missing `{header}`")

    if name in PHASE1_SKILLS:
        checks = [
            "## Shared Blocks (Phase 1)",
            f"{LOCAL_SHARED_PREFIX}runtime-draft-guardrails.md",
            f"{LOCAL_SHARED_PREFIX}style-and-brief-preambles.md",
            f"{LOCAL_SHARED_PREFIX}greeting-name-resolution.md",
            f"{LOCAL_SHARED_PREFIX}personalization-check.md",
        ]
        for needle in checks:
            if not _contains(text, needle):
                errors.append(f"{rel}: missing Phase 1 reference `{needle}`")
        for local_name in (
            "runtime-draft-guardrails.md",
            "style-and-brief-preambles.md",
            "greeting-name-resolution.md",
            "personalization-check.md",
        ):
            local_path = path.parent / "references" / "shared" / local_name
            if not local_path.exists():
                errors.append(f"{rel}: missing local shared file `{local_path.relative_to(path.parent)}`")

    if name in PHASE2_SKILLS:
        checks = [
            "## Shared Blocks (Phase 2)",
            f"{LOCAL_SHARED_PREFIX}runtime-draft-guardrails.md",
            f"{LOCAL_SHARED_PREFIX}style-and-brief-preambles.md",
        ]
        if name != "kol-brief-sender":
            checks.append(f"{LOCAL_SHARED_PREFIX}reply-envelope-contract.md")
        for needle in checks:
            if not _contains(text, needle):
                errors.append(f"{rel}: missing Phase 2 reference `{needle}`")
        needed_local = [
            "runtime-draft-guardrails.md",
            "style-and-brief-preambles.md",
        ]
        if name != "kol-brief-sender":
            needed_local.append("reply-envelope-contract.md")
        for local_name in needed_local:
            local_path = path.parent / "references" / "shared" / local_name
            if not local_path.exists():
                errors.append(f"{rel}: missing local shared file `{local_path.relative_to(path.parent)}`")

    if name in PHASE3_SKILLS:
        phase3_header = (
            "## Shared References (Phase 3)"
            if name == "kol-outreach-orchestrator-flow"
            else "## Shared Blocks (Phase 3)"
        )
        checks = [
            phase3_header,
            f"{LOCAL_SHARED_PREFIX}bridge-runtime-core.md",
            f"{LOCAL_SHARED_PREFIX}router-dispatcher-boundaries.md",
        ]
        for needle in checks:
            if not _contains(text, needle):
                errors.append(f"{rel}: missing Phase 3 reference `{needle}`")
        for local_name in ("bridge-runtime-core.md", "router-dispatcher-boundaries.md"):
            local_path = path.parent / "references" / "shared" / local_name
            if not local_path.exists():
                errors.append(f"{rel}: missing local shared file `{local_path.relative_to(path.parent)}`")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (defaults to parent of scripts/).",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    skill_root = repo_root / "skills" / "social-media"
    skill_files = sorted(skill_root.glob("kol-*/SKILL.md"))
    errors: list[str] = []

    for shared_rel in sorted(SHARED_FILES):
        shared_path = repo_root / shared_rel
        if not shared_path.exists():
            errors.append(f"{shared_rel}: missing shared file")

    if not skill_files:
        errors.append("No KOL SKILL.md files found under skills/social-media/kol-*/")
    else:
        for skill_file in skill_files:
            errors.extend(_check_file(skill_file, repo_root))

    checked = len(skill_files)
    if errors:
        print(f"KOL skill consistency: FAILED ({len(errors)} issue(s), {checked} file(s) checked)")
        for issue in errors:
            print(f"- {issue}")
        return 1

    print(f"KOL skill consistency: OK ({checked} file(s) checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
