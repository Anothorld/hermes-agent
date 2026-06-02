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

Follow `kol-reply-dispatcher/references/shared/reply-envelope-contract.md` and
persist via `persist-reply-draft` (same as other child skills).

## Pitfalls

- Do not treat repeat KOLs like cold prospects — acknowledge history briefly.
- Do not open with pricing before scope unless `learning_hints` allow it for this goal.
