#!/usr/bin/env python3
"""Migrate KOL SKILL.md shared refs to Hermes-native skill-local references.

Rewrites:
  ../kol/_shared/<file>.md  ->  references/shared/<file>.md

Then ensures each referenced shared file exists inside the skill directory:
  <skill>/references/shared/<file>.md
"""

from __future__ import annotations

import re
from pathlib import Path


PATTERN = re.compile(r"\.\./kol/_shared/([A-Za-z0-9\-]+\.md)")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    skills_root = repo_root / "skills" / "social-media"
    shared_root = skills_root / "kol" / "_shared"
    skill_files = sorted(skills_root.glob("kol-*/SKILL.md"))

    touched = 0
    copied = 0
    for skill_md in skill_files:
        original = skill_md.read_text(encoding="utf-8")
        basenames = sorted(set(PATTERN.findall(original)))
        if not basenames:
            continue

        updated = PATTERN.sub(r"references/shared/\1", original)
        if updated != original:
            skill_md.write_text(updated, encoding="utf-8")
            touched += 1

        shared_dest_dir = skill_md.parent / "references" / "shared"
        shared_dest_dir.mkdir(parents=True, exist_ok=True)
        for name in basenames:
            src = shared_root / name
            if not src.exists():
                raise FileNotFoundError(f"Missing source shared doc: {src}")
            dst = shared_dest_dir / name
            content = src.read_text(encoding="utf-8")
            if not dst.exists() or dst.read_text(encoding="utf-8") != content:
                dst.write_text(content, encoding="utf-8")
                copied += 1

    print(f"updated SKILL.md files: {touched}")
    print(f"copied shared docs: {copied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
