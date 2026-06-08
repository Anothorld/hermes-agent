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

## Style + brief preambles (mandatory)

Build the prompt header before drafting (see
`references/shared/style-and-brief-preambles.md`): invoke
`kol-email-style-loader` (pass `--owner-user-id <current_user_id>` when the run
carries one; `user_style` without an owner returns an empty block, never an
error), then `kol-creator-brief-loader`. Loader failures must not block drafting.

## Output

Return subject/body/to in `child_envelope`. Parent run persists via
`persist-initial-outreach-draft` (first-touch after shortlist) or
`persist-reply-draft` when replying in an existing Gmail thread.

**Body format (hard):** HTML only — every paragraph in `<p>…</p>`, the product
as a real `<a href="<product_url>">…</a>` link (never plain text or a bare URL).
Set `html: true` and `kind: initial_outreach`. The body is sent verbatim as the
operator's Gmail draft; a plain-text paragraph or missing product link is a
defect (POVISON 683). Apply `humanizer` in email mode as the final pass,
preserving the HTML structure and product link.

Use native **terminal** for bridge CLI — never `execute_code` + subprocess or HTTP.

## Pitfalls

- Do not treat repeat KOLs like cold prospects — acknowledge history briefly.
- Do not open with pricing before scope unless `learning_hints` allow it for this goal.
- Do not return a plain-text body or omit the product `<a href>` link — HTML is required.
