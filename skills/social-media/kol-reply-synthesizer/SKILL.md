---
name: kol-reply-synthesizer
description: Merges multi-goal reply fragments into one coherent email body.
trigger: Invoked by kol-reply-dispatcher after parallel fragment-mode child skills return topic-only prose.
tags: ["kol", "synthesis", "reply", "multi-goal", "draft-generator"]
---

## Goal
Turn ordered **topic-only fragments** from parallel fragment-mode child
skills into **one** outbound email body with a single greeting and
sign-off. The dispatcher enriches `to` / `subject` / `thread_id` before
persistence — this skill outputs **body content only**.

## Shared Blocks
- `references/shared/runtime-draft-guardrails.md`
- `references/shared/style-and-brief-preambles.md`
- `references/shared/reply-envelope-contract.md`

## Runtime Contract
- **One greeting, one sign-off.** Fragments must not be pasted with their
  own salutations — strip any accidental "Hi …" / "Best," from inputs.
- **Preserve order.** Merge fragments in the order supplied (lane + goal
  priority from dispatcher).
- **No new facts.** Do not invent numbers, SKUs, or deliverable counts
  not present in the fragments or `thread_history`.
- **Exclude gated topics.** Fragments marked `excluded: true` (human gate)
  must not appear in the merged body.
- **Do not send mail.** Return content-only envelope `{body, thread_id?}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`
2. `latest_email` — inbound message (`from`, `subject`, `body`)
3. `thread_history` — verbatim from dispatcher Step 0
4. `fragments` — ordered list:
   ```json
   [
     {"lane": "commerce", "goal": "product_selection",
      "skill": "kol-product-selector", "fragment": "..."},
     {"lane": "commerce", "goal": "deliverables_scope",
      "skill": "kol-deliverables-clarifier", "fragment": "..."}
   ]
   ```
5. Optional `operator_style_preamble` from `kol-email-style-loader`

- Learning hints (reject few-shot from dispatch context):
  `../kol-reply-dispatcher/references/shared/learning-hints.md`

## Email Style Preamble (mandatory)
>>> include: kol-email-style-loader

## Procedure

### Step 1 — Normalize fragments
Drop any item with `excluded: true` or empty `fragment`. Strip leading
greetings/sign-offs from each remaining fragment.

### Step 2 — Compose body
Structure:
1. One greeting addressing the inbound sender (from `latest_email.from`).
2. One short opener acknowledging their reply (optional, ≤1 sentence).
3. Merge fragment prose in order — use paragraph breaks between topics.
4. One closing line + sign-off matching style preamble.

Hard rules:
- Do not repeat the same fact in two paragraphs unless clarifying a
  contradiction the KOL raised.
- When a fragment mentions compensation and another mentions scope, scope
  paragraphs precede compensation paragraphs (match fragment order).
- Keep total length reasonable (≤250 words unless fragments require more).

### Step 3 — Return envelope
```json
{
  "skill": "kol-reply-synthesizer",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "<from inbound>",
  "body": "<merged body only>"
}
```

Do **not** set `to` or `subject`.

## Examples

### Success — product + deliverables
Fragments: product confirmation + scope framework. Output: one email
thanking the KOL, confirming Aurora-Power variant, then stating IG+TT
deliverable framework — single "Best, POVISON Team" at the end.

### Failure — empty fragments
All fragments excluded or empty → return
`{"error": "no_fragments_to_synthesize"}` so dispatcher opens escalation.

## Pitfalls
- Duplicating greetings from child fragments ("Hi Alyssa" twice).
- Adding a budget number not present in any compensation fragment.
- Reordering fragments against dispatcher order (breaks lane priority).
