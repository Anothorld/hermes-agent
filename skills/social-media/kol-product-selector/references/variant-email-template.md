# Variant email template (kol-product-selector)

KOL-facing product-selection emails must use **variant candidates** from
`campaign_config.variant_candidates` (fallback: `# product_variants` block
in `extra_notes`). Internal ids live in `sku_whitelist` only.

## Allowed in email body

- `campaign_config.product_display_name`
- Human-readable spec text from each candidate's `attributes` or `label`
- Candidate `url` (one link per option)

## Forbidden in email body

- `variant_id`, numeric entity ids (`37384`, etc.)
- Merchant SKU codes (`SF8181G265`, etc.)
- The word `SKU:` followed by a code
- Internal keys like `offer.proposed_skus` values copied verbatim

## Email templates

### Branch B — propose options (KOL has not picked yet)

```
For this collab we'd love to send you one of these options:

1. <product_display_name> — <size / material / color>
   View option: <url>

2. ...

Which works best for your space?
```

Pick 1–3 candidates whose `id` is in `sku_whitelist`. Write their ids to
`offer.proposed_skus` internally — never paste ids into the email.

### Branch A — confirm (KOL named an allowed option)

Match KOL text against candidate `attributes` and `label` (fuzzy on color/size).
When matched, confirm using **product name + spec + url** only. Write
`offer.sku_locked=<candidate.id>` and
`offer.color_or_variant_locked=<human color/spec or null>`.

### Branch C — out-of-policy / off-whitelist request

Do **not** counter-propose automatically. Open manual approval:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py open-escalation \
  --env <TEST|LIVE> \
  --json '{
    "identity_id": <id>,
    "campaign_id": "<campaign_id>",
    "goal": "product_selection",
    "reason": "KOL requested variant outside whitelist/policy",
    "question_to_operator": "<what KOL asked for vs allowed options>",
    "severity": "normal"
  }'
```

Return `{"escalation_opened": true, "id": ...}` and **do not** return a
draft `body` for auto-send.

## Loading candidates

```
get-dispatch-context → campaign_config.variant_candidates
```

If empty, parse `campaign_config.extra_notes` `# product_variants` JSON.
If still empty and `sku_whitelist` is empty → abort
`campaign_config_incomplete`.
