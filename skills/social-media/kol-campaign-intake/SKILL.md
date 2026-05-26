---
name: kol-campaign-intake
description: Parses operator-supplied free-text campaign briefs (web form, dispatch chat, email forward) into a structured `campaign_config` blob and persists it via the bridge. Required field guard: `product_display_name`, `sku_whitelist`, `color_variant_policy`, `compensation`, `deliverable_platforms`, `deliverable_count_per_platform`, `brief_template_id`, `audit_standards_md`. Optional: `boost_required`, `boost_meta_partner_code`, `no_watermark_required`, `required_mentions`, `required_hashtags`, `followup_intervals`, `extra_notes`. Refuses to create the campaign with placeholder values; asks the operator for any missing required field.
trigger: Invoked from the web "new campaign" page or by `kol-reply-dispatcher` when the operator routes a brief to it via a dispatch thread (deferred). Not on the KOL-facing reply path.
tags: ["kol", "campaign", "config", "intake", "meta-lane"]
---

## Goal
Turn free-text into a strict `campaign_config` row. Block creation
when required fields are missing or ambiguous; never paper over with
defaults that bypass downstream guards (sku whitelist enforcement,
audit standards, etc.).

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **No silent defaults for safety-critical fields.** Missing
  `product_display_name`, `sku_whitelist`, `color_variant_policy`,
  `audit_standards_md`, or `compensation` MUST short-circuit with a
  specific list of missing fields. Do not default `sku_whitelist=[]`
  (that effectively blocks every product), `color_variant_policy="any"`
  (that defeats the whole policy), or `product_display_name=<sku>` /
  `product_display_name=<campaign_id>` (that defeats the cold-outreach
  SKU-leak guard — the whole point of this field is to give downstream
  email skills a human-friendly product reference that is **not** a
  catalog code).
- **Idempotent on `campaign_id`.** If `campaign_config` already
  exists for that id, abort `{"error":"campaign_exists","campaign_id":"..."}`.
- **No KOL identity writes.** This skill only touches `campaigns` /
  `campaign_config` rows.
- **Compensation cap sanity:** if
  `compensation.paid_max_amount` is set, must be > 0 and < an
  operator-configurable absurdity ceiling (e.g. 1,000,000 USD).
  Above that → ask for confirmation explicitly.

## Inputs
1. `campaign_id` (string, kebab-or-ts code), `env`.
2. `raw_brief` — free text from operator (markdown / plain).
3. Optional `extracted_overrides` — structured fields the operator
   explicitly provided alongside the free text (preferred over
   raw_brief parsing when present).

## Procedure

### Step 1 — Pre-validation
- Fetch existing campaign_config; abort if exists.
- Initialize `parsed = {}`, `missing = []`.

### Step 2 — Extract per field (priority: overrides > raw_brief)

For each REQUIRED field, attempt extraction in this order:
1. `extracted_overrides[field]` if present and well-typed.
2. Best-effort parse from `raw_brief` (LLM extraction step happens
   in the calling layer; this skill receives a JSON candidate it can
   validate).
3. If still empty/None → append to `missing[]`.

**Validators:**
- `product_display_name`: non-empty string, 2–80 chars, **must not**
  match the SKU regex `^[A-Z]{2,5}[\- ]?\d{3,5}[A-Z0-9]*$` and **must
  not** equal `campaign_id` (case-insensitive) or any entry in
  `sku_whitelist`. Examples of good values: `"the new media console"`,
  `"POVISON Atlas sofa"`, `"our 2026 spring rug"`. Examples that fail:
  `"SEB800"`, `"TS8319"`, `"POV-RUG-04"`.
- `sku_whitelist`: non-empty list of strings, each matching the
  POVISON SKU regex (or whatever brand-specific regex is configured).
  Empty list → missing.
- `color_variant_policy`: one of `{strict_whitelist, locked_per_kol, any_in_whitelist}`.
- `compensation`: object with `default_mode ∈ {gifted, paid, commission, hybrid}`
  and the relevant numeric fields (paid_max_amount, commission_pct, etc.).
- `deliverable_platforms`: non-empty list, each ∈
  `{instagram, tiktok, youtube, twitter, blog}`.
- `deliverable_count_per_platform`: dict of platform → positive int.
- `brief_template_id`: non-empty string referencing an existing
  template id (optional existence check via the templates endpoint;
  if endpoint absent, accept the string but flag a soft warning).
- `audit_standards_md`: non-empty string ≥ 50 chars (reject empty
  placeholder).

### Step 3 — Branch

| Condition | Action |
|---|---|
| `missing` non-empty | abort with `{"error":"campaign_config_incomplete","missing":[...]}`; the calling UI shows the operator a focused prompt |
| any value fails type/regex check | abort with `{"error":"campaign_config_invalid","field":"...","reason":"..."}` |
| `paid_max_amount > absurdity_ceiling` AND no `confirmed_high_budget=true` flag | abort with `{"error":"compensation_cap_review_required","amount":N}` |
| all green | proceed to Step 4 |

### Step 4 — Persist
Use the deterministic bridge CLI (never hand-roll HTTP or SQL) to
upsert the campaign config atomically:
```
python <PROJECT>/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
  upsert-campaign --env TEST|LIVE --campaign-id <id> --json @/tmp/campaign.json
```
The JSON body mirrors `CampaignUpsertBody`: `{title, paid_ceiling,
contract_required, sku_whitelist, brief_template_id, ...}`. The CLI
already injects `X-Bridge-Key` from `HERMES_KOL_OPS_BRIDGE_KEY`. The
endpoint is atomic (creates the campaign row + config row in one
transaction, or none).

Do NOT write any `kol_facts.*` (this skill is identity-agnostic).

### Step 5 — Return result envelope
```json
{
  "skill": "kol-campaign-intake",
  "branch_action": "created | rejected_incomplete | rejected_invalid | rejected_cap_review",
  "campaign_id": "TS8319",
  "env": "TEST",
  "config_keys_set": ["sku_whitelist", "compensation", ...],
  "warnings": ["brief_template_id not verified against templates endpoint"]
}
```

## Examples

### Created
operator pastes: "Campaign TS8319, IG×1 + TT×1, gifted-first, paid up
to $400, sku rugs/2026/*, lock color per KOL once chosen, brief tmpl
brief-rugs-2026, audit standards: must include #ad in first sentence,
no political / dietary claims, watermark allowed but no-watermark
preferred for boost, boost via meta partner POV-PARTNER-2026."
Skill validates, writes, returns `branch_action="created"`.

### Incomplete
operator forgot `audit_standards_md`. Skill returns
`{"error":"campaign_config_incomplete","missing":["audit_standards_md"]}`.

### Cap review
`paid_max_amount=2_000_000`. Skill returns
`{"error":"compensation_cap_review_required","amount":2000000}`. UI
prompts operator: "are you sure?". Re-run with
`extracted_overrides.confirmed_high_budget=true`.

## Pitfalls
- Defaulting `product_display_name` to the SKU or campaign code. The
  whole reason this field exists is so cold-outreach has a human name
  to put in the email; auto-filling it with `SEB800` re-introduces the
  exact leak this guard prevents. If the operator only typed a SKU,
  reject and ask for a friendly name.
- Defaulting `sku_whitelist=[]` (silently blocks all products
  downstream) or `color_variant_policy="any"` (silently disables the
  product-selector's safety net). Always require explicit values.
- Treating `audit_standards_md = ""` as "no standards required" — that
  un-grounds the content reviewer. Reject empty.
- Persisting partial configs. Step 4 must be atomic.
- Updating an existing campaign via this skill. There should be a
  separate `kol-campaign-update` flow for amendments.
