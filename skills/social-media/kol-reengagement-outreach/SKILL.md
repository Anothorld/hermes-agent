---
name: kol-reengagement-outreach
description: Generates the FIRST outreach email draft for a REPEAT KOL ("repeat_kol" path). Reads campaign config + identity facts + relationship history (last_outcome, preferred_skus, preferred_mode, default_shipping_address) via the Bridge dispatch-context, composes a warm "back again" opening that references the prior collab and proposes the most likely next collab shape, writes draft-ready facts (`offer.outreach_draft_ready=true`, `offer.outreach_path=reengagement`) via the Bridge, and returns the draft envelope as JSON for the caller to persist. Never sends mail directly.
trigger: Invoked by `kol-discovery-to-outreach-router` for candidates assigned `identity.outreach_path=reengagement` (i.e. `relationship_status=repeat_kol`, NOT `repeat_kol_needs_review` — those open an escalation instead). Or on demand when the user says "draft a re-engagement email to <handle>".
tags: ["kol", "outreach", "reengagement", "repeat-collab", "draft-generator", "commerce-lane"]
---

## Goal
Produce one warm re-engagement email that
1. acknowledges the prior collab,
2. proposes a concrete next collab consistent with prior mode +
   preferred SKUs,
3. asks ONE confirmation question (default: "shipping info same as
   before?" if `default_shipping_address` is on file),
and atomically records the outreach on the Bridge. No reply handling,
no Gmail send.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. `--env <TEST|LIVE>` mandatory.
- **Refuses risky repeats.** If
  `relationship.last_outcome ∈ {disputed, content_failed, aborted}`,
  abort and return
  `{"skipped":"needs_review","delegate_to":"escalation"}`; the router
  is supposed to open an escalation in that case, not call this
  skill. Defense-in-depth.
- **Single-shot per (identity, campaign).** If
  `offer.outreach_sent=true` already, abort with `{"skipped":"already_sent"}`.
  If `offer.outreach_draft_ready=true` already, abort with
  `{"skipped":"draft_already_ready"}`.
- **No price talk in the opening.** May reference prior mode
  ("happy to do another gifted collab" / "if commission works again
  for you") but does NOT quote numbers.
- **Reuses identity-level facts.** Default shipping address /
  preferred mode / preferred SKUs are loaded from
  `reusable_facts` and used as soft defaults the KOL can confirm in
  one reply.

## Inputs
1. `identity_id` (mandatory).
2. `campaign_id` (mandatory).
3. `env` (`TEST` or `LIVE`, mandatory).

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. The loader returns a single markdown block enforcing
**P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "reengagement_outreach", missing_facts: ["offer.outreach_draft_ready"], next_action: "<one-line summary referencing prior collab>"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the **first section** of the draft prompt — before any
  goal-specific instructions in this skill's Procedure.
- failure mode: if the loader fails, use empty-doc fallbacks and continue.

>>> include: kol-email-style-loader

## Creator Brief Preamble (mandatory before drafting)

Immediately after the style-loader block, this skill **MUST** also invoke
`kol-creator-brief-loader` and prepend its output as a `[P0.1]` section so
the LLM can tie the proposed next-collab back to one specific element of
the creator's content style. Even for repeat KOLs we re-personalize each
opening — the prior collab is shared history, but the creator's content
angle is what makes the proposal land.

Call contract:
- inputs: `identity_id`, `env`, optional `campaign_id`.
- output: markdown block with `content_pillars`, `signature_hooks`,
  `voice_descriptors`, `hero_post_url`, `hero_post_note`,
  `recommendation_reason`, and `brief_status ∈ {fresh|refreshed|unavailable}`.
- order in the final prompt: `[P0]` → `[P0.1] creator brief` → `[P1]` → `[P2]` → `[P3]`.
- failure mode: loader never throws; on `unavailable` the drafter (Step 2)
  emits `low_personalization: true` in the envelope.

>>> include: kol-creator-brief-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Required signals:
- `goals.outreach.status == "active"` (else abort `already_sent`).
- `relationship.total_collabs >= 1` (else abort
  `{"skipped":"not_a_repeat_kol","delegate_to":"kol-cold-outreach"}`).
- `relationship.last_outcome ∉ {disputed, content_failed, aborted}` —
  these MUST go through escalation; do not draft for them.
- `identity.display_name` (from `get-identity`) — the KOL's real
  name if it's already on file. Always **prefer** this over the
  handle for the greeting.
- `campaign.product_display_name` — operator-friendly product name
  (visible anchor text or plain mention).
- `campaign.product_url` — product page URL. When present, the
  product mention MUST be rendered as
  `<a href="{product_url}">{product_display_name}</a>`. When empty,
  render plain text.
- `reusable_facts` keys to use:
  - `identity.default_shipping_address` (decides the confirm question).
  - `identity.preferred_skus` (mention in proposal if present).
  - `identity.preferred_mode` (gifted / paid / commission / hybrid).
- `candidate.payload` (NEW) — per-campaign discovery evidence. Often
  `null` for repeat KOLs added through the relationship-resolution
  router rather than a fresh discovery pass; treat as optional.
- `identity_facts` (NEW) — all identity-level facts. The 6 creator
  brief keys (`identity.content_pillars`, `identity.signature_hooks`,
  `identity.voice_descriptors`, `identity.hero_post_url`,
  `identity.hero_post_note`, `identity.recommendation_reason`) back
  the `[P0.1]` brief block and should anchor the Step 2 paragraph 2
  product proposal.

### Step 1b — Resolve the KOL's greeting name (mandatory)

Use this priority order to pick the **first name** for the salutation.
Stop at the first hit:

1. `identity.display_name` is set → take its first whitespace-separated
   token (e.g. `"Becki Owens"` → `Becki`).
2. `reusable_facts['identity.first_name']` is set → use it verbatim.
3. Otherwise, **parse** `identity.primary_handle` into a likely
   `First Last`, then take the first token:
   - Strip a leading `@` and any trailing digits/underscores.
   - If the handle contains `.`, `_`, `-`, or space, split on that
     separator: `"becki_owens"` → `["becki","owens"]`.
   - Otherwise attempt a CamelCase / known-name split. For mixed case
     (`"BeckiOwens"`), split at the second capital. For all-lowercase
     (`"beckiowens"`), only split when a 3+ char common English first
     name prefix matches and leaves a 3+ char remainder.
   - Title-case the result and take the first token (`"beckiowens"`
     → `Becki Owens` → `Becki`).
   - If no confident split, fall back to the title-cased whole handle
     (`"beckiowens"` → `Becki`). Never use the lowercase id verbatim.
4. If still unresolvable (numeric handle, empty), open an escalation
   `kol_name_unresolvable` and abort.

The greeting MUST be the **first name only** — never include the
last name in `Hi, …`. Correct: `Hi Becki,`. Wrong: `Hi Becki Owens,`,
`Hi beckiowens,`, `Hi @beckiowens,`.

### Step 2 — Compose the email
Constraints:
- Subject: warm, references continuity. Example:
  "Round 2? <Brand> × <FirstName>" or
  "Back with another POVISON drop for you". Plain text — no HTML in
  the subject.
- **Body format: HTML.** Wrap paragraphs in `<p>…</p>` and use `<br>`
  for forced line breaks. When `campaign.product_url` is present the
  product mention MUST be an anchor tag
  (`<a href="{product_url}">{product_display_name}</a>`); use the URL
  verbatim. Set `html: true` in the draft envelope (Step 4).
- Body: 3–5 short paragraphs.
  1. Greeting + appreciation reference to the prior collab. Use the
     first name resolved in Step 1b: `<p>Hi {FirstName},</p>`. If
     `last_outcome` is `success` or `success_with_revisions`, say so
     warmly in one line.
  2. Concrete proposal: cite up to 2 items from `preferred_skus`, or
     "another piece similar to what worked last time" if absent.
     When linking the proposed product, prefer the anchor form above.
     Match prior `preferred_mode` ("happy to do gifted again" /
     "if commission works for you again"); never escalate the mode
     unsolicited. **Tie the proposal to one specific element of the
     creator brief** when the brief is available — e.g., if
     `content_pillars` includes "cozy hosting" and the proposed
     product is the Atlas sofa, connect them in one short clause
     ("…thinking it would slot naturally into your hosting tours").
     This is what distinguishes a re-engagement opening from a
     transactional reorder.
  3. ONE confirmation question — preferably:
     "Shipping info same as before — `<masked address one-liner>`?"
     when `default_shipping_address` is present. Otherwise:
     "Would you be up for it? Happy to share more if so."
     **Mask** the address in the email: show city/country only,
     never the full street; the address is just for the KOL to
     confirm/correct, not for us to broadcast it.
  4. Sign-off.
- Allowed HTML tags: `<p>`, `<br>`, `<a href="…">`, `<strong>`,
  `<em>`. No images, no inline styles. Anchor `href` values MUST be
  `http://` or `https://` URLs only.
- Do NOT ask for new deliverables/platforms here — defer to
  `kol-deliverables-clarifier` after the reply.

### Step 2c — Personalization post-check (mandatory)

Before Step 3, verify the body incorporates the creator brief.

**When `brief_status ∈ {fresh, refreshed}`:**
1. Build a set of substantive tokens from the brief facts:
   - All tokens of length ≥ 4 from `identity.content_pillars[*]`.
   - All tokens of length ≥ 4 from `identity.signature_hooks[*]`.
   - All tokens of length ≥ 5 from `identity.recommendation_reason`
     after stripping common filler words (same stoplist as
     `kol-cold-outreach` Step 2c).
2. Strip HTML tags from `body` to get visible text.
3. Case-insensitive substring match: count how many tokens appear.
4. If **zero tokens match**, re-generate the draft ONCE with this
   instruction prepended:
   *"Your previous draft did not reference any detail from the [P0.1]
   creator brief. Rewrite paragraph 2 (the proposal) so it ties the
   proposed product to one specific pillar, hook, or hero-post theme
   from the brief — in one short clause."*
5. Re-run the personalization check on the retry. Still zero matches
   → abort:
   ```json
   {"error":"personalization_check_failed",
    "brief_status":"<fresh|refreshed>",
    "tokens_expected":[...],
    "field":"body"}
   ```

**When `brief_status == "unavailable"`:**
Skip the token check. Step 4 envelope MUST include
`low_personalization: true` and `low_personalization_reason:
"creator_brief_unavailable"`.

### Step 3 — Write outbound facts (single call)
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-reengagement-outreach",
            "namespaces":{
              "offer":    {"offer.outreach_draft_ready": true,
                            "offer.outreach_path": "reengagement",
                            "offer.proposed_mode": "<gifted|paid|commission|hybrid>",
                            "offer.proposed_skus": ["sku-a","sku-b"]},
              "identity": {"identity.last_outreach_draft_at": "<iso8601>"}
            }}'
```

`offer.proposed_mode` and `offer.proposed_skus` are intentionally
captured so the downstream
`kol-product-selector` / `kol-compensation-negotiator` skills can read
them without re-deriving from prose. Omit `offer.proposed_skus` if
no preferred SKUs were on file.

### Step 4 — Return draft envelope
```json
{
  "skill": "kol-reengagement-outreach",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "subject": "Round 2? POVISON × Becki",
  "body": "<p>Hi Becki,</p><p>...<a href=\"https://povison.com/products/atlas-sofa\">the Atlas sofa</a>...</p>",
  "html": true,
  "to": "<resolved from identity.primary_email>",
  "thread_id": null,
  "address_confirm_asked": true,
  "facts_written": {"offer": 4, "identity": 1},
  "brief_status": "fresh",
  "personalization_tokens_matched": ["cozy hosting"]
}
```

When `brief_status == "unavailable"`, the envelope MUST include
`low_personalization: true` and `low_personalization_reason:
"creator_brief_unavailable"` in place of `personalization_tokens_matched`.

`html: true` is mandatory whenever the body contains anchor tags so
the bridge sends the Gmail draft with a `text/html` MIME part.

`thread_id: null` because we always start a fresh thread for a fresh
campaign — the prior collab thread is a different campaign.

## Examples

### Success — same mode, same SKUs, same address
KOL `@alice`, `total_collabs=1`, `last_outcome=success`,
`preferred_mode=gifted`, `preferred_skus=["povi-rug-04"]`,
`default_shipping_address` present. Step 2 composes a warm 4-para
email proposing "another piece similar to the rug from last time" +
ONE address-confirmation question masked to "shipping to your London
address?". Step 3 writes 4 offer facts + 1 identity fact in one call.

### Failure — risky repeat
`last_outcome="content_failed"`. Skill aborts with
`{"skipped":"needs_review","delegate_to":"escalation"}` — the router
should have caught this upstream and opened an escalation instead.

### Failure — actually a new prospect
`total_collabs=0`. Skill aborts with
`{"skipped":"not_a_repeat_kol","delegate_to":"kol-cold-outreach"}`.

## Pitfalls
- Never quote prices, commission percentages, or deliverable counts
  in the reengagement email. Continuity ≠ blanket-renewal of terms.
- Never let a repeat-collab opening read like a transactional reorder.
  The creator brief is what differentiates "we'd love to send another
  rug your way" (generic) from "we'd love to send another rug your
  way — feels like a natural fit for the slow-morning shots you've
  been doing lately" (anchored). Paragraph 2 must tie the proposal to
  one brief detail when `brief_status ∈ {fresh, refreshed}`.
- Never set `brief_status: "unavailable"` to skip the personalization
  check when the loader actually returned a brief. The loader's status
  field is authoritative; mirror it exactly.
- Never greet the KOL by their last name or full raw handle.
  `Hi beckiowens,` / `Hi @beckiowens,` / `Hi Becki Owens,` are all
  wrong; use `Hi Becki,` (first name only, resolved via Step 1b).
- Never invent a `product_url`. If `campaign.product_url` is empty,
  render the product mention as plain text.
- Never set `html: false` while keeping anchor tags in `body` — the
  draft will be sent as `text/plain` and the KOL will see raw markup.
- Do not output the full default shipping address verbatim — mask to
  city/country in the email body. The structured address stays in
  `kol_identity` for downstream skills to read.
- Do not silently bump the mode (e.g. last collab gifted, this email
  proposing paid). Mode escalations belong to the
  `kol-compensation-negotiator` after the reply.
- Do not redraft if `outreach_sent=true`; abort with `already_sent`.
  Do not redraft if `outreach_draft_ready=true`; abort with
  `draft_already_ready`.
- Do not call `cal.py` / direct SQL / `execute_code`.
