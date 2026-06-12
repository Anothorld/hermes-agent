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
- **Do not send mail.** Return content-only envelope `{body, thread_id?,
  conversation_summary?}`.
- **`conversation_summary` (mandatory on inbound reply drafts):** 3–8
  Chinese bullet points for operators — derived only from `latest_email`,
  `thread_history`, and fragment context. Never invent facts.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`
2. `latest_email` — inbound message (`from`, `subject`, `body`)
3. `thread_history` — verbatim from dispatcher Step 0
4. Optional `summary_only: true` — when **explicitly** set, skip body
   synthesis and return only `conversation_summary` (single-skill /
   escalation / refine paths before persist). `fragments=[]` without
   `summary_only: true` is an error — do not infer summary-only mode.
5. `fragments` — ordered list (ignored when `summary_only: true`):
   ```json
   [
     {"lane": "commerce", "goal": "product_selection",
      "skill": "kol-product-selector", "fragment": "..."},
     {"lane": "commerce", "goal": "deliverables_scope",
      "skill": "kol-deliverables-clarifier", "fragment": "..."}
   ]
   ```
6. Optional `operator_style_preamble` from `kol-email-style-loader`

- Learning hints (reject few-shot from dispatch context):
  `../kol-reply-dispatcher/references/shared/learning-hints.md`

## Email Style Preamble (mandatory)
>>> include: kol-email-style-loader

## Procedure

### Step 1 — Normalize fragments
When `summary_only` is true, skip to Step 2b (no body). Otherwise drop
any item with `excluded: true` or empty `fragment`. Strip leading
greetings/sign-offs from each remaining fragment.

### Step 2 — Compose body
Skip when `summary_only` is true.
Structure:
1. One greeting addressing the inbound sender (from `latest_email.from`).
2. One short opener acknowledging their reply (optional, ≤1 sentence).
3. Merge fragment prose in order — use paragraph breaks between topics.
4. One closing line + sign-off matching style preamble.

Hard rules:
- Do **not** embed prior thread quotes in `body` (`On … wrote:`, `>` lines,
  or pasted `thread_history`) — dispatcher persist strips them; approve adds
  one Gmail quote.
- Do not repeat the same fact in two paragraphs unless clarifying a
  contradiction the KOL raised.
- When a fragment mentions compensation and another mentions scope, scope
  paragraphs precede compensation paragraphs (match fragment order).
- Keep total length reasonable (≤250 words unless fragments require more).

### Step 2b — Compose operator conversation summary (mandatory)
Always produce `conversation_summary.bullets` — **Chinese**, 3–8 short
lines (≤80 chars each when possible), plain language for non-technical
operators. Cover only what appears in `latest_email`, `thread_history`,
or merged fragments:

1. **合作阶段** — current negotiation stage (product, deliverables, budget, etc.)
2. **本封信 KOL 诉求** — what they asked or stated in the latest inbound
3. **双方已确认** — facts both sides already agreed (from thread + fragments)
4. **待确认 / 风险** — open questions or blockers (omit section if none)

When `thread_history` is `[]`, include a bullet such as
「首次收到 KOL 回复，此前无邮件往来记录」.

Do **not** copy email quotes into bullets. Do **not** invent SKUs, rates,
or commitments not evidenced in inputs.

### Step 3 — Return envelope
**Normal mode** (body + summary):
```json
{
  "skill": "kol-reply-synthesizer",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "<from inbound>",
  "body": "<merged body only>",
  "conversation_summary": {
    "bullets": [
      "合作阶段：正在确认产品与交付范围",
      "本封信 KOL 诉求：询问 IG+TT 各 1 条报价",
      "双方已确认：对产品 Aurora-Power 有兴趣",
      "待确认：预算区间与交付时间"
    ]
  }
}
```

**`summary_only` mode** (no `body`):
```json
{
  "skill": "kol-reply-synthesizer",
  "summary_only": true,
  "conversation_summary": {"bullets": ["...", "..."]}
}
```

Do **not** set `to` or `subject`.

## Examples

### Success — product + deliverables
Fragments: product confirmation + scope framework. Output: one email
thanking the KOL, confirming Aurora-Power variant, then stating IG+TT
deliverable framework — single "Best, POVISON Team" at the end.

### Failure — empty fragments
All fragments excluded or empty (and **not** `summary_only`) → return
`{"error": "no_fragments_to_synthesize"}` so dispatcher opens escalation.

### Success — summary_only for single-skill persist
`fragments=[]`, `summary_only=true` → return only `conversation_summary`
for dispatcher / escalation / refine paths that drafted via one child skill.

## Pitfalls
- Duplicating greetings from child fragments ("Hi Alyssa" twice).
- Adding a budget number not present in any compensation fragment.
- Reordering fragments against dispatcher order (breaks lane priority).
- English summary bullets — operators read Chinese on the Approvals page.
- Omitting `conversation_summary` on a normal inbound reply synthesis.
