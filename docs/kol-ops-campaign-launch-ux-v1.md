# KOL OPS Console Campaign Launch UX V1

## Scope

- Product URL is required when creating/updating product catalog entries.
- Variant options are auto-detected from product URL and stored as product candidate variants.
- Campaign launch uses campaign-level variant whitelist (`product_variant_ids`) as the only downstream selection scope.
- Launch flow adds preflight validation, conflict recovery (`force=true` retry), and clearer error copy.

## Frontend-Backend Mapping

| Frontend Input | API Field | Rule |
|---|---|---|
| Product URL | `products.url` | required, valid `http(s)` URL |
| Variant candidate rows | `products.variants` | parsed from URL + manual rows merged, deduped by `id` |
| Campaign whitelist selection | `campaigns.start.product_variant_ids` | must be subset of `products.variants[*].id` |
| Display name | `campaigns.start.product_display_name` | non-empty, not SKU-shaped, not equal to SKU/campaign id |
| TEST recipient | `campaigns.start.test_mode_to` | required only when `env=TEST` |
| Deliverable platforms | `campaigns.start.deliverable_platforms` | at least one required |
| Deliverable count | `campaigns.start.deliverable_count_per_platform` | integer >= 1 |

## Validation Matrix (V1)

### Hard blockers

- Product URL missing/invalid.
- Viewer role attempting to launch campaign.
- `env=TEST` but `test_mode_to` empty.
- `product_pitch_md` empty.
- `product_display_name` empty, SKU-shaped, or collides with SKU/campaign id.
- Variant candidates exist but whitelist selection is empty.
- No deliverable platform selected.
- `deliverable_count_per_platform < 1`.

### Warnings

- `env=LIVE` launch risk.
- `audit_standards_md` empty.
- Product has no candidate variants.

## API Error to UX Copy

| API status/code | UX copy |
|---|---|
| `400` + `product_url_required` | Please update product catalog with valid product URL before launch. |
| `409` | Existing running launch conflict; offer `force` retry confirm. |
| `422` | Field validation failed with specific guidance. |
| `502` + `cal_upsert_failed` | CAL write failed; retry later or contact on-call. |

## Rollout Plan

### Phase 1 (enabled now)

- Product URL required + URL format validation.
- Auto parse variants from URL in product form.
- Campaign whitelist explicit controls (select all / clear).
- Launch preflight panel and LIVE second confirmation.
- 409 conflict recovery with optional `force=true` retry.
- Viewer cannot execute launch.

### Phase 2

- Stepper interaction and richer preflight details.
- Whitelist batch operations by attribute filters.
- Audit standards templates and deliverable presets.

### Phase 3

- Align compensation fields (`mode`, `commission_band`) with CAL upsert schema.
- Enforce downstream "whitelist-only consumption" guards across all selection tools.

### Phase 3 (implemented in this round)

- Added launch-time compensation inputs: `compensation_mode`, `commission_min_pct`, `commission_max_pct`.
- Added backend validation for commission fields when mode is `commission` or `hybrid`.
- Mapped compensation values into CAL upsert payload:
  - `barter_policy <- compensation_mode`
  - `commission_band <- {min,max}` ratio converted from percent.
- Added backend guard `variant_whitelist_required` when product has variants but launch payload omits `product_variant_ids`.

## Metrics

- Launch 422 rate.
- Launch conflict recovery success (`409 -> force retry` conversion).
- Discovery-to-shortlist cycle time.
- Escalations caused by missing launch config.

## Rollback

- Revert frontend preflight-only constraints first (non-destructive).
- Keep backend URL validation and campaign URL guard unless incident requires temporary bypass.
- If bypass is needed, gate with explicit owner decision and track audit event.
