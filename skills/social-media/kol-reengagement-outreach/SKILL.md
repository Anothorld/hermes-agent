---
name: kol-reengagement-outreach
description: Warm re-engagement email for repeat KOLs (outreach path=reengagement).
tags: ["kol", "outreach", "reengagement", "relationship"]
---

# kol-reengagement-outreach

Selected by `kol-reply-dispatcher` when `meta.path=reengagement` on the outreach goal.

## Relationship personalization (required read)

Before drafting, load dispatch context and read:

- `reusable_facts.facts.personalization_hint` — 1–2 sentences from prior collabs
  (`last_outcome`, `preferred_mode`, `negotiation_style`).
- `learning_hints` — operator reject patterns (see
  `kol-reply-dispatcher/references/shared/learning-hints.md`).

Weave hints naturally; do not repeat rejected phrasing.

## Output

Return subject/body/to in `child_envelope`. Parent run persists via
`persist-initial-outreach-draft` (cold/first-touch after shortlist) or
`persist-reply-draft` when replying in an existing Gmail thread.

Use native **terminal** for bridge CLI — never `execute_code` + subprocess or HTTP.

## Pitfalls

- Do not treat repeat KOLs like cold prospects — acknowledge history briefly.
- Do not open with pricing before scope unless `learning_hints` allow it for this goal.
