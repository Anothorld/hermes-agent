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

## Memory layers (four-store model)

| Store | Role | Authoritative for |
|-------|------|-------------------|
| CAL `learning_hints` | Approved operator-learning policies (goal-sliced) | Email tactics, style, discovery rules |
| Skill `references/learned/` | Promoted playbooks after repeated approval | Stable advisory wording |
| CAL `personalization_hint` | Structured relationship fields | Repeat-KOL tone, prior outcome |
| Hindsight | Cross-session episodic recall (prefetch + tools) | Past conversation context, entity links |
| MEMORY.md / USER.md | Profile meta notes | Tool prefs, operator communication style |

**Do not** store email tactics or approved policies in Hindsight retain or MEMORY.md.
Those flow through the Console learning pipeline → CAL policy → `learning_hints`.

### Hindsight (episodic, advisory)

1. Read prefetch-injected Hindsight context when present (hybrid mode).
2. If episodic gaps remain, call `hindsight_recall` with `@handle` + `campaign_id`
   from the brief's `# hindsight_recall_seed` block (or dispatch context).
3. Hindsight is **advisory only** — never override `learning_hints`, pricing
   engine output, fact ownership, or escalation gates.
4. **Do not** call `hindsight_retain` for email templates, negotiation tactics,
   or discovery criteria (Console learning owns those).

## How child skills must use it

1. After loading dispatch context, read `learning_hints.hints`.
2. Treat `reject_event` and `reply_learning` hints as **negative few-shot** —
   patterns the operator already rejected or corrected.
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
6. **Priority on conflict:** fact ownership > pricing_engine output >
   escalation/anomaly gates > this skill's HARD rules > **learning hints** >
   `references/learned/<goal>.md` (if promoted) > CAL **`personalization_hint`**
   > **Hindsight recall** (episodic) > MEMORY.md / USER.md.
   Hints and Hindsight are advisory — never use them to override the above or
   invent numbers/SKUs/deliverables.
7. If `learning_hints.hints` is empty, proceed normally (Hindsight may still
   supply episodic context).

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
It outranks Hindsight episodic recall but not `learning_hints` or pricing/fact
ownership.
