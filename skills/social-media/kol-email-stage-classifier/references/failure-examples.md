# Classifier failure examples (regression seed)

Use these patterns when the classifier over-commits facts. Cross-check with
`playground/classifier_eval/run_eval.py` after edits.

## Premature interest confirmation

**Inbound:** "What deliverables do you need? Still interested but need details."

**Wrong:** `offer.interest_signal=confirmed` without `interest_positive`.

**Right:** `needs_more_info` + signals `asks_deliverables`, `interest_positive`
(if warmth is explicit).

## Budget question ≠ deliverables locked

**Inbound:** "What's the budget for one Reel?"

**Wrong:** `offer.deliverable_platforms` committed.

**Right:** rewrite to `offer.deliverable_platforms_proposed` or omit until
`accepts_terms`.

## Rate quote ≠ agreed terms

**Inbound:** "My rate is $1,500 for the scope."

**Wrong:** `offer.agreed_terms` without `accepts_terms`.

**Right:** `offer.kol_paid_quote=1500`, `proposes_rate` signal only.

## OOS SKU inquiry

**Inbound:** "Do you have the larger size in walnut?"

**Wrong:** `offer.sku_locked` on a non-whitelisted SKU.

**Right:** drop lock keys; emit `requests_oos_sku` or `requests_color_swap`.

## Operator correction loop

When `GET /learning/fact-corrections` shows a manual override of an
`email:*` fact, add a one-line example here before the next classifier
prompt change.
