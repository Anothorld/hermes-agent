---
name: kol-pricing-strategist
description: Pure decision spec for compute-compensation-offer. Returns target_number, bounds, wording, human-gate, and negotiation_phase. Used by kol-compensation-negotiator via the bridge CLI; never invoked by the dispatcher directly.
trigger: Called via `kol_bridge_tool.py compute-compensation-offer` from `kol-compensation-negotiator`. Inputs are passed by the parent skill. This skill does NOT call get-dispatch-context.
tags: ["kol", "pricing", "negotiation", "decision-only", "no-side-effects", "commerce-lane"]
---

## Goal
Document the deterministic pricing contract implemented in
`plugins/kol-ops-bridge/pricing_engine.py`. The parent
`kol-compensation-negotiator` calls the bridge CLI and handles all side
effects.

## Runtime Contract
- **Pure function** (implemented in Python, not LLM).
- Output is ONE JSON object (see schema below).
- All branches must populate every key (use `null` where N/A).
- **Over-ceiling quotes never escalate** — always auto-counter with
  internal-review / campaign-economics wording when the anchor is high.

## Inputs (parent passes inline)
```json
{
  "mode": "gifted | paid | commission | hybrid",
  "kol_quoted_amount": 1800.0,
  "kol_quoted_currency": "USD",
  "kol_quoted_basis": "flat | per_post | percent",
  "campaign_config": {
    "product_unit_price": 200.0,
    "barter_policy": "barter_first | barter_optional | none",
    "paid_ceiling": 1500.0,
    "paid_target_budget": 500.0,
    "commission_band": {"min_pct": 8.0, "max_pct": 12.0,
                         "cookie_days": 30,
                         "attribution": "last_click"},
    "deliverable_count_per_platform": 1,
    "deliverable_platforms": ["instagram", "tiktok"]
  },
  "relationship": {
    "preferred_mode": "gifted | paid | commission | hybrid | unknown",
    "avg_revision_rounds": 1.2,
    "last_outcome": "success | success_with_revisions | ..."
  },
  "contact_type": "direct | agency | manager",
  "identity": {"contact_role": "kol | agency | manager | assistant"},
  "identity_integrity": "matched | drifted | delegated | unknown",
  "follower_count": 120000,
  "creator_tier": "koc | mid_tier | top_tier",
  "candidate": {"payload": {"followers": "12万", "kol_tier": "mid_tier"}},
  "identity_facts": {"identity.follower_count": 120000},
  "barter_attempted": false,
  "rate_requested": false,
  "paid_hold_sent": false,
  "prior_proposed_amount": 500,
  "kol_insists_paid": false
}
```

Read `barter_attempted`, `rate_requested` from dispatch context
`campaign_facts` (or legacy `paid_hold_sent` as alias for `rate_requested`).

## Semantics (HARD)
- **`kol_quoted_amount` = pure cash supplement** the KOL wants on top of the
  gifted product. It is **not** an all-in rate that includes product value.
- **`product_unit_price`** = retail/product value used in barter anchoring and
  in counter wording ("product at no cost, retail ~$X").
- **`paid_ceiling`** = maximum **cash supplement** we can approve (not total
  deal value).
- **`paid_target_budget`** (optional) = ideal cash supplement when the KOL has
  not quoted yet and the starting anchor for first cash counters.
- **`prior_proposed_amount`** (optional) = our latest cash offer. When present,
  the next counter increases only by the configured small step.
- **`creator_tier` / `follower_count`** (optional) = audience-size signals
  for KOC / mid-tier / top-tier pricing. Explicit tier wins over follower
  count. If absent, the engine uses the legacy budget-only strategy.
- After barter-first, counters use `mode_decided=hybrid` when
  `product_unit_price` is set (gifted product + cash supplement).
- `mode=hybrid` and `mode=paid` share the same post-barter cash path.
  Prefer `mode=paid` when the classifier emits it; both are supported.

## Audience tier strategy

The engine derives tier from explicit `creator_tier` / `kol_tier`, then from
follower aliases in `candidate.payload`, `identity_facts`, or top-level
payload:

| Tier | Followers | First cash counter | Negotiation frame |
|---|---:|---|---|
| `koc` | `<50k` | about quote × `0.50` | product value, official case-building, strict first-test budget |
| `mid_tier` | `50k-300k` | quote × `0.50-0.55` | vertical creator benchmark, ROI pressure, workflow simplicity |
| `top_tier` | `>300k` | about quote × `0.60` | schedule efficiency, controlled revisions, clean approvals |

Tier strategy never grants extra product, rights, or scope concessions. If the
final gap is small, hold the cash line and emphasize process simplicity /
revision control rather than non-cash sweeteners.

### First cash counter wording template (English)

When `prior_proposed_amount` is absent, the engine returns this inaugural
counter spine (direct KOL variant; agency uses a formal leadership variant):

> Because this is our first single-campaign test together, my manager has
> locked the category's one-time intro budget very tightly — the ceiling for
> this round is only `{currency} {target}`.
> I genuinely want to help get this partnership approved on our side. If the
> conversion results from this single test meet our baseline, we'll absolutely
> prioritize you for future multi-campaign collaborations with us. Would you be open
> to supporting us on this first round and moving forward at `{currency} {target}`?

Do **not** mention the KOL's quoted rate or a percentage discount in the
email body — the ratio is used internally for counter math only. Do **not**
name a specific future quarter or timeline; keep follow-on priority vague
("future" / "later" multi-campaign work).

When `product_unit_price` is set, a separate gifted-product sentence is
prepended. Later counters switch to shrinking-concession wording.

## Decision matrix

### Direct KOL (contact_type=direct, or role=kol, or integrity matched/drifted)

Tone: friendly and creator-facing, but still designed to lower the cash ask.
Use warmer wording ("feels workable", "keep this smooth on your side") while
anchoring on product value, focused scope, and a lean cash supplement.

| Phase | Condition | Decision |
|---|---|---|
| Barter first | `barter_attempted=false` + paid/hybrid/commission signal | `mode_decided=gifted`, `negotiation_phase=barter_first` |
| Rate request | `barter_attempted=true` + no quote + insists paid | `negotiation_phase=rate_request`; ask KOL for best **cash supplement** |
| Await quote | `barter_attempted=true` + `rate_requested=true` + no quote | stay on `rate_request`; do **not** counter yet |
| Paid counter | quote present after barter (even if `rate_requested=false`) OR `rate_requested=true` + quote | auto-counter; high quotes use campaign-economics wording |
| Paid counter (round-1 quote) | `barter_attempted=true` + quote on record + insists paid | **skip rate_request**; counter immediately (expected) |

### Agency / manager

Tone: professional and commercial. Use campaign economics, incremental cash
fee, aligned deliverables, scope control, revision efficiency, and timing as
the negotiation frame. The cash number remains firm and low.

| KOL signal | mode | Decision |
|---|---|---|
| Paid-only / rate card | `paid` | skip barter-first; auto-counter |
| Quote > paid_ceiling | `paid` | auto-counter with campaign-economics wording (no escalation) |
| Commission within band | `commission` | accept or counter toward lower band edge |
| Commission > max_pct | `commission` | counter at `commission_band.max_pct` |

### Paid counter ratios and pacing (within ceiling band)

- Legacy/no-tier first cash counter: `paid_target_budget` if set, else
  `paid_ceiling × 0.4`.
- Tiered first cash counter: max of the campaign floor and the tier's quote
  ratio, then capped by `paid_ceiling × 0.65`.
- Later counters: `prior_proposed_amount + paid_counter_increment`
  (`100` by default) in legacy/no-tier mode. In tiered mode, increases shrink
  by round (e.g. `500 → 650 → 720 → 750`) to show budget exhaustion.
- Quote/ceiling cap still applies:
  `min(candidate, quote × tier_cap, paid_ceiling × 0.65)`.
- Legacy counters round **down** to natural anchors (hundreds/tens).
  Tiered counters round down to precise-looking anchors and avoid exact
  hundreds (e.g. `600 → 580`, `1200 → 1180`). Do not output decimals.
- `lower_bound = paid_target_budget` when set, else `paid_ceiling × 0.4`
- Never counter at exactly `paid_ceiling`

The parent can override via `paid_ratio_override`.

## Output schema (mandatory)
```json
{
  "mode_decided": "gifted | paid | commission | hybrid",
  "target_number": 800,
  "target_basis": "flat | per_post | percent | null",
  "target_currency": "USD",
  "lower_bound": 600,
  "upper_bound": 1500,
  "suggested_wording": "...",
  "requires_human_gate": false,
  "gate_reason": null,
  "rationale_one_line": "...",
  "negotiation_phase": "barter_first | rate_request | paid_counter | escalate | null"
}
```

`requires_human_gate=true` only for structural gaps (`missing_paid_ceiling`,
`missing_commission_band`, …) — **not** for high quotes.

## Examples

### Direct KOL — barter first despite paid quote
Input: `mode=paid`, `kol_quoted_amount=1500`, `barter_attempted=false`,
`contact_type=direct`. Output: `negotiation_phase=barter_first`.

### Direct KOL — round-1 quote, round-2 insists paid
Input: `mode=paid`, `kol_quoted_amount=1500`, `barter_attempted=true`,
`kol_insists_paid=true`. Output: `negotiation_phase=paid_counter` (no
rate_request).

### Direct KOL — ask for rate (no quote yet)
Input: `barter_attempted=true`, no quote, `kol_insists_paid=true`.
Output: `negotiation_phase=rate_request`.

### Agency — high quote auto-counter
Input: `mode=paid`, `kol_quoted_amount=1800`, `paid_ceiling=1500`,
`contact_type=agency`. Output: `target_number=600`, campaign-economics wording,
`requires_human_gate=false`.

### Mid-tier creator — precise first counter
Input: `mode=paid`, `kol_quoted_amount=2000`, `creator_tier=mid_tier`,
`paid_target_budget=500`, `paid_ceiling=2000`, `contact_type=agency`.
Output: first counter around `USD 1080` (quote × 0.55, rounded away from a
round hundred), with one-off test ROI wording.

### Tiered pacing — shrinking concessions
Input: mid-tier, prior offers `500`, then `650`, then `720`.
Output: next counters should move roughly `650 → 720 → 750`, never jump to
the cap and never reveal `paid_ceiling`.

## Pitfalls
- Escalating on over-ceiling quotes — always counter instead.
- Treating `rate_request` as `requires_human_gate=true`.
- Countering direct KOL before barter-first on first paid signal.
- Counter at exactly `paid_ceiling` (reveals the cap).
- Adding non-cash sweeteners / extra product / scope concessions to close
  small gaps — hold cash line and simplify process instead.
