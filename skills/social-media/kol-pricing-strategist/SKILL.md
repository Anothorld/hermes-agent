---
name: kol-pricing-strategist
description: Pure decision skill — no side effects, no Bridge writes. Given (campaign_config pricing fields, KOL's quote/mode signal, relationship history), returns a structured JSON recommendation: target_number, lower_bound, upper_bound, suggested_wording, mode, requires_human_gate (bool), gate_reason. Used as a sub-skill by `kol-compensation-negotiator`; never invoked by the dispatcher directly.
trigger: Called inline by `kol-compensation-negotiator`. Inputs are passed via the calling skill, not via CLI. This skill does NOT call get-dispatch-context; the parent supplies all necessary facts so this skill stays deterministic and testable.
tags: ["kol", "pricing", "negotiation", "decision-only", "no-side-effects", "commerce-lane"]
---

## Goal
Given a structured pricing situation, return a structured pricing
recommendation. No emails drafted, no facts written, no escalations
opened. The parent `kol-compensation-negotiator` interprets the
recommendation and handles all side effects.

## Runtime Contract
- **Pure function.** No CLI calls, no HTTP, no file writes, no Gmail.
- Output is ONE JSON object (see schema below). No prose.
- All branches must populate every key (use `null` where N/A) so the
  parent skill can rely on a stable shape.

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
  }
}
```

## Decision matrix

| KOL signal | mode | Decision |
|---|---|---|
| No quote, no mode signal | `gifted` | barter only; target=null; wording emphasizes product value |
| "I work only paid" + quote ≤ paid_ceiling | `paid` | counter at `unit_price × paid_ratio` (default ratio: `min(1.0, paid_ceiling / kol_quoted_amount)`); if `kol_quoted_amount ≤ unit_price`, first try gifted-only |
| "I work only paid" + quote > paid_ceiling | `paid` | `requires_human_gate=true`, `gate_reason="paid_quote_over_ceiling"` |
| "commission-based" / "share %" + quote within band | `commission` | accept inside band; include cookie + attribution |
| "commission-based" + quote > max_pct | `commission` | counter at `commission_band.max_pct`; if KOL refuses → `requires_human_gate=true` |
| Product + cash supplement | `hybrid` | barter + small cash by tier (≤ unit_price → 0; ≤ 2×unit_price → unit_price × 0.3; else escalate) |
| Product + commission | `hybrid` | barter + commission within band |

For `paid_ratio` we recommend a default of `0.6` of `paid_ceiling`
when KOL's quote is unknown; otherwise `min(quote * 0.7, paid_ceiling)`.
The parent can override by passing `paid_ratio_override`.

## Output schema (mandatory)
```json
{
  "mode_decided": "gifted | paid | commission | hybrid",
  "target_number": 1050.0,
  "target_basis": "flat | per_post | percent | null",
  "target_currency": "USD",
  "lower_bound": 800.0,
  "upper_bound": 1500.0,
  "suggested_wording": "Thanks for the rate. We can stretch to ...",
  "requires_human_gate": false,
  "gate_reason": null,
  "rationale_one_line": "KOL quote 1800 > ceiling 1500 → counter at 1050 (=ceiling × 0.7)."
}
```

When `requires_human_gate=true`:
- `target_number` MAY be null (do not pre-commit a number that
  exceeds policy).
- `suggested_wording` SHOULD be the holding line:
  "Let me check internally and come back to you on this — usually 1-2
  business days." (parent skill will open an escalation, not send
  this verbatim).

## Examples

### Gifted default
Input: `mode=gifted`, no quote, `barter_policy=barter_first`. Output:
`mode_decided=gifted`, `target_number=null`, wording emphasizes product
value, `requires_human_gate=false`.

### Paid over ceiling
Input: `mode=paid`, `kol_quoted_amount=1800`, `paid_ceiling=1500`.
Output: `requires_human_gate=true`,
`gate_reason="paid_quote_over_ceiling"`, `target_number=null`.

### Commission within band
Input: `mode=commission`, KOL asks 10%, band `{min:8, max:12}`.
Output: `mode_decided=commission`, `target_number=10.0`,
`target_basis="percent"`, `lower_bound=8.0`, `upper_bound=12.0`,
wording confirms cookie/attribution.

## Pitfalls
- Returning prose instead of JSON. Parent will fail to parse.
- Using `null` for `mode_decided` — always pick the most likely mode.
- Silently lowering `paid_ceiling` to fit KOL quote. The whole point
  of the gate is policy enforcement.
- Counter at exactly `paid_ceiling` (KOL learns the cap). Use 0.6-0.8
  of ceiling as default counter.
