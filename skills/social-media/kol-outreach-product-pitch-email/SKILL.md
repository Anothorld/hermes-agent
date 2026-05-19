---
name: kol-outreach-product-pitch-email
description: Draft (never send) the second-touch English email that introduces 2-4 SKUs from the campaign whitelist to a KOL who replied positively or asked for materials. Validates every URL/SKU id against the whitelist and escalates instead of inventing products.
trigger: When the reply dispatcher classifies a KOL reply as intent `interested` or `asks_materials` with confidence >= 0.7, and the KOL has no existing `draft_ids.product_pitch` on their Kanban card.
tags: ["kol", "outreach", "email", "product-pitch", "gmail", "draft"]
---

## Goal
Draft one Gmail reply on the KOL's existing thread that proposes **2 to 4** concrete products from the campaign's `sku_whitelist`, with a short reason each, and a soft ask about which one(s) resonate. Keep total body ≤ 200 words. Drafts only.

## Inputs (from caller)
- `campaign_id` and config path.
- Kanban card id (must contain `gmail_thread_id`, `selling_point_group`, `creator_type`, `kol_handle`).
- The KOL's reply text (for context, not for parsing rules).

If `gmail_thread_id` is missing or empty, abort and escalate — never start a new thread for a product pitch.

## Procedure

### Step 1 — Load and validate whitelist
1. Read `config.yaml` → `sku_whitelist`.
2. Parse each whitelist entry into `{sku_id, url, host}`.
3. Reject any entry whose host is not in the brand's known hosts (configured under `config.yaml: allowed_hosts`, default = host of the first whitelist URL). If rejected, **escalate** and stop; do not silently drop entries.

### Step 2 — Pick 2-4 SKUs
Selection rules, in order:
1. Prefer SKUs whose tags match the card's `selling_point_group`.
2. Prefer SKUs not already pitched on this thread (check the thread's prior drafts for whitelist URLs).
3. Cap at **4** SKUs even if more match; cap at **2** when `creator_type` is `micro` (< 50k followers) to avoid overload.
4. If fewer than 2 valid SKUs remain after filtering, escalate to chat with the message `Not enough whitelisted SKUs for @<handle>; need human pick` and stop.

### Step 3 — Compose body (English, reply on thread)
Subject: leave Gmail's auto `Re: <previous subject>`. Do not override it.

Body skeleton:

```
Hi <first_name_or_handle>,

Thanks so much for getting back to me! Based on your content I think these could be a good fit:

1. <product_name> — <one short line tying it to selling-point group>
   <url>

2. <product_name> — ...
   <url>

[3-4 optional]

Curious which (if any) speak to you — once we land on a piece, I can share more on creative direction and timing.

<brand_signature>
```

Hard content rules:
- **Only URLs from `sku_whitelist`.** Every URL in the draft body MUST be present, byte-for-byte, in the whitelist. Run this check programmatically before calling `drafts.create`. If a URL fails the check, abort and escalate.
- **No price in the email.** Negotiation belongs in the next round.
- **No "I'll send a contract"**, no exclusivity language, no deadline pressure.
- **No attachments.** Reviewers see drafts cleanest without them.

### Step 4 — Create / update Gmail draft
1. Use `gmail drafts.create` with `threadId = gmail_thread_id` so the draft appears as a reply on the existing thread.
2. In TEST MODE, rewrite `to` to `test_mode_to` and prepend `Intended recipient: <real_email>` on the first body line.
3. Apply label `kol-outreach/pending/product_pitch` to the draft message.

### Step 5 — Write back to card

```yaml
draft_ids:
  product_pitch: <draft_id>
status: drafted_product_pitch
last_pitched_skus:
  - <sku_id_1>
  - <sku_id_2>
last_action_at: <iso8601>
```

### Step 6 — Return
Return `{draft_id, kol_handle, skus_pitched}` to caller. Do not notify; the orchestrator / dispatcher batches notifications.

## Hard Rules
- Never invent a SKU, product name, or URL. Whitelist is the only source of truth.
- Never start a new thread; always reply on `gmail_thread_id`.
- Never include more than 4 SKUs or fewer than 2.
- Never quote a price or fee in this email.
- Never send. `drafts.create` / `drafts.update` only.

## Pitfalls
- Do not infer SKU tags from the URL slug if the whitelist YAML carries explicit `tags`; trust the YAML.
- Do not re-pitch SKUs already linked earlier in the thread.
- Do not switch language to Chinese even if the KOL's reply is mixed-language; the campaign is NA / English.
- Do not bypass the whitelist with "looks like the same product on a different page"; escalate instead.
