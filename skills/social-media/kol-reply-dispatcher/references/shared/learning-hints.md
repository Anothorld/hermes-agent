# Learning hints (runtime injection)

`get-dispatch-context --view agent` returns a `learning_hints` object:

```json
{
  "hints": [
    {"source": "policy", "scope": "reply_learning", "content": "..."},
    {"source": "policy", "scope": "reply_strategy", "content": "..."},
    {"source": "policy", "scope": "company_style", "content": "..."},
    {"source": "reject_event", "goal": "...", "tags": [], "note": "...", "snippet": "..."}
  ],
  "active_goals": ["compensation_negotiation"]
}
```

## How child skills must use it

1. After loading dispatch context, read `learning_hints.hints`.
2. Treat each hint as a **negative few-shot** — patterns the operator already
   rejected or corrected.
3. Do **not** repeat phrasing called out in `note`, `suggested_fix`, or policy
   bullets for the same `goal` / `child_skill`.
4. `reply_strategy` hints are **tactical** (sequencing, when to discuss price,
   barter-first cues) distilled from operator Gmail edits — obey them for the
   active goal like `reply_learning` reject patterns.
5. `company_style` hints are **cross-goal tone/phrasing** distilled from
   operator final-draft edits. They mirror what `kol-email-style-loader`
   injects; honor them for wording/length but they never add facts. (Disable
   this hint channel with `KOL_STYLE_IN_HINTS=0` if the style-loader already
   covers it.)
6. Priority on conflict: **fact ownership > pricing_engine output >
   escalation/anomaly gates > this skill's HARD rules > learning hints**.
   Hints are advisory only — never use them to override the above or invent
   numbers/SKUs/deliverables.
7. If `learning_hints.hints` is empty, proceed normally.

## Operator reject payload (Console → Bridge)

Reject with structured correction:

```json
{
  "identity_id": 42,
  "campaign_id": "C1",
  "env": "LIVE",
  "decided_by": "operator:alice",
  "correction": {
    "tags": ["premature_pricing", "too_long"],
    "note": "Do not mention price before scope is clear",
    "suggested_fix": "Ask which deliverables they prefer first"
  }
}
```

POST ` /approvals/approval.reply_draft/reject` with `X-Bridge-Key`.

## Repeat-KOL personalization

`reusable_facts.facts.personalization_hint` carries 1–2 sentences derived
from `kol_relationship` (prior outcome, preferred_mode, negotiation_style).
Weave it into re-engagement / warm replies when `total_collabs > 0`.
