# Contract field sourcing (POVISON template)

Reference for Branch I field assembly. The bridge `POST /contracts/render`
endpoint runs `contract_product.enrich_contract_fields()` **after** the agent
submits JSON — several fields are overwritten deterministically from CAL facts
and campaign config. The agent still must supply a complete JSON payload, but
should **not fight** the enricher on product link/specs, shipping identity,
social URLs, dates, or deliverables (see below).

## Sales reference vs common agent mistakes

Use the operator-approved docx (e.g. Megan McLeod / SEB8008) as the visual
gold standard. Typical gaps when the agent improvises:

| Contract area | Sales-approved pattern | Common wrong output | Fix |
|---|---|---|---|
| Legal name | From shipping blob (`Megan McLeod`) | IG display name (`Megan Allen`) | Enricher reads `fulfillment.shipping_address` first token |
| Phone / address | Full line + country | Empty or partial | Parsed from shipping blob + `identity.region` |
| Intro date | `June 16, 2026` | `2026-06-16` | Enricher sets `date_long` / `date_short` |
| Signature date | `6/16/2026` | ISO date | Renderer uses `date_short` for `${DATE}` |
| Social links | Full `https://…` URLs | Bare handles | Enricher normalizes from handle |
| YouTube unused | `/` | blank | Set `youtube: "/"` or let enricher |
| Advertiser contact | Candice Wilson (template) | Johnny Miller (old template) | Template ships Candice; do not override |
| Product specs | `Aurora-Power Sofa Bed (Color: …/ Size: …/ SKU: …)` | `SKU · marketing name · color` | Enricher sales-format from variant attrs |
| Product link | Variant URL (`?variant=41550`) | Campaign default (`40300`) | **Escalate** if locked color has no catalog row |
| Cash section | Absent for gifted deals | Section 2 left in doc | Set `fee: null`; renderer drops section 2 |
| Deliverables table | Template default 2 rows (+ Ad Codes) | One hand-built row | **Omit** `deliverables` unless campaign has stored spec |

## Required agent inputs (Step I.1)

Still required in JSON even when enricher overwrites:

- `identity_id`, `campaign_id`, `env`
- `influencer.email` (from dispatch context)
- `fee`: `null` for gifted / product-only; `{amount, currency}` only when
  `offer.compensation_mode` includes a flat cash fee
- Do **not** pass `deliverables` when `campaign_deliverables_json` is empty —
  the template’s default rows (video/stories/RAW + ad codes) are kept

## Deterministic enricher behavior (`contract_product.py`)

On render, bridge loads latest facts + campaign config and overlays:

1. **Influencer** — `fulfillment.shipping_address` → `full_name`, `phone`,
   `address`; social URLs from `identity.primary_handle`; YouTube → `/` when
   absent.
2. **Dates** — `date` ISO + `date_long` (intro) + `date_short` (signature).
3. **Product** — `offer.color_or_variant_locked` + variant catalog →
   `product.link` and sales-format `product.specs`. Catalog merges campaign
   `# product_variants` with live Povison parse
   (`product_variants.parse_variants_from_url`, same as Console
   `POST /products/parse-variants`) to resolve ``merchant_sku``, color, size,
   and ``?variant=`` id. Falls back to ``campaign_config.product_url`` only when
   no row matches locked color.
4. **Deliverables** — only when `campaign_deliverables_json` is non-empty;
   otherwise key removed so template defaults remain.

## Escalate before render

Open escalation `contract_fields_incomplete` or `contract_variant_unresolved` when:

- `fulfillment.shipping_address` missing and no structured
  `identity.default_shipping_address`
- Locked color/variant has no row in product catalog / `# product_variants`
  **and** `campaign_config.product_url` points at a different variant
- `compensation_mode` is cash/hybrid but `offer.agreed_terms` has no numeric fee
- Operator expects a custom deliverables table but
  `campaign_deliverables_json` was never saved at campaign launch

## Deliverables source of truth

```
GET /campaigns/{campaign_id}/resolved-deliverables?env=<env>
```

- `has_stored_spec: true` → enricher injects `rows` automatically; agent may
  omit `deliverables`.
- `has_stored_spec: false` → **omit** `deliverables` in JSON; do not synthesize
  from `deliverable_platforms` alone (produces a single generic row and drops
  Ad Codes).

## Variant catalog gap (data fix, not skill)

Render-time enrich **calls the Povison variant API** via ``product_variants.py``
(Console ``POST /products/parse-variants`` uses the same module). When the API
is reachable, locked colors like ``Light Chenille Brown`` resolve to the correct
``merchant_sku`` (e.g. ``SEB8008K295``) and ``?variant=41550`` without manual
catalog edits. If the API fails, escalate for operator catalog refresh — do not
hand-pick URLs in the JSON payload.
