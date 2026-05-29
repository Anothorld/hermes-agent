# KOL shared skill blocks

This folder contains reusable policy blocks for KOL outreach skills.

Phase 1 scopes:
- `kol-cold-outreach`
- `kol-reengagement-outreach`

Phase 3 shared docs:
- `bridge-runtime-core.md`
- `router-dispatcher-boundaries.md`

Usage pattern:
- Keep skill-specific logic in each skill's own `SKILL.md`.
- Reference these shared blocks for repeated guardrails and contracts.
- When a shared rule changes, update it here first, then verify skill docs
  still align with the updated contract.

Consistency check:
- Run `python scripts/check_kol_skill_consistency.py` from repo root.
