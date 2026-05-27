---
name: kol-cold-outreach
description: Generates the FIRST outreach email draft to a brand-new KOL ("new_prospect" path). Reads campaign config + identity facts via the Bridge dispatch-context, composes a concise open-ended collab introduction (no compensation mechanism, no price, no contract talk), writes draft-ready facts (`offer.outreach_draft_ready=true`, `offer.outreach_path=cold`) via the Bridge, and returns the draft envelope as JSON for the caller to persist. Never sends mail directly. Does not handle replies — the next inbound flows back through `kol-reply-dispatcher`.
trigger: Invoked by `kol-discovery-to-outreach-router` for candidates assigned `identity.outreach_path=cold` (i.e. `relationship_status=new_prospect` who were just selected for outreach), or on demand when the user says "draft a cold outreach to <handle>". Never auto-runs from a cron — only chained.
tags: ["kol", "outreach", "cold", "first-contact", "draft-generator", "commerce-lane"]
---

## Goal
Produce one well-targeted opening email to a new KOL — open-ended
brand-introduction tone, no compensation mechanism, no quotes, no
contract terms — and atomically record on the Bridge that an
operator-reviewable draft is ready. No reply handling, no threading
state, no Gmail send.

## Runtime Contract
- Profile: `outreach-operator`.
- Bridge is the only CAL writer. Forbidden: `cal.py`, direct
  `~/.hermes/kol-ops-bridge/cal.db`, ad-hoc SQL, `execute_code`. Use
  `plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` (deterministic CLI)
  or HTTP endpoints. `--env <TEST|LIVE>` mandatory.
- **Drafts no email send.** This SKILL produces the draft envelope; a
  separate persistence step (Gmail draft creation, operator review)
  lives outside this SKILL and is deferred to a later phase.
- **Single-shot:** This skill runs once per (identity, campaign). If
  `offer.outreach_sent` is already true, abort and return
  `{"skipped": "already_sent"}`. If `offer.outreach_draft_ready` is
  already true, abort and return `{"skipped": "draft_already_ready"}`.
- **No compensation mechanism talk:** never mention paid collaboration,
   barter/exchange, gifted/free product, compensation, paid rate,
   commission percentage, or contract terms. The opening keeps the
   cooperation model open so later negotiation has room; compensation is
   owned by `kol-compensation-negotiator`.
- **No deliverable counts:** never commit deliverable counts or
  platforms in this email; deliverables are framed only as "we'd love
  to discuss together". `kol-deliverables-clarifier` owns that
  conversation.

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
- inputs: `goal_brief = {goal: "cold_outreach", missing_facts: ["offer.outreach_draft_ready"], next_action: "<one-line summary of this email's purpose>"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the **first section** of the draft prompt — before any
  goal-specific instructions in this skill's Procedure.
- failure mode: if the loader fails (bridge unreachable / policy doc
  missing), use empty-doc fallbacks (`(no company-wide style configured)` /
  `(no personal style configured)`) — **never block** the draft.

>>> include: kol-email-style-loader

## Creator Brief Preamble (mandatory before drafting)

Immediately after the style-loader block, this skill **MUST** also invoke
`kol-creator-brief-loader` and prepend its output as a `[P0.1]` section so
the LLM has concrete content/style material to personalize the opening
"why-them" line.

Call contract:
- inputs: `identity_id`, `env`, and (optional) `campaign_id` for
  provenance.
- output: a markdown block listing `content_pillars`, `signature_hooks`,
  `voice_descriptors`, `hero_post_url`, `hero_post_note`,
  `recommendation_reason`, and `brief_status ∈ {fresh|refreshed|unavailable}`.
- order in the final prompt: `[P0]` → `[P0.1] creator brief` → `[P1]` → `[P2]` → `[P3]`.
- failure mode: the loader never throws — on total failure it returns a
  block with `Brief status: unavailable`. The drafter (Step 2) detects
  that state and emits `low_personalization: true` in the envelope.

>>> include: kol-creator-brief-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```

Use:
- `goals.outreach.status` — must be `active`. If `satisfied` already,
  abort with `{"skipped": "already_sent"}`.
- `relationship.total_collabs` — must be 0. If > 0, abort with
  `{"skipped": "not_a_new_prospect", "delegate_to": "kol-reengagement-outreach"}`;
  do NOT silently switch tone.
- `reusable_facts['identity.primary_handle']`, `identity.region`,
  `identity.language` — to localize the salutation.
- `identity.display_name` (from `get-identity`) — the KOL's real name
  if it's already on file. Always **prefer** this over the handle.
- `campaign.product_display_name` — the **operator-friendly product
  name** (e.g. "the new media console", "POVISON Atlas sofa"). This is
  the **only** acceptable visible product reference in the email body.
  If this field is empty/null, fall back to a generic category phrase
  (`"our new piece"`, `"a new release"`); **never** substitute the
  `campaign_id`, `sku_whitelist[*]`, or any internal model code as the
  visible text.
- `campaign.product_url` — the product page URL. When present, the
  visible product name in Step 2 MUST be rendered as an HTML
  hyperlink (`<a href="{product_url}">{product_display_name}</a>`).
  If `product_url` is missing/null, render the product name as plain
  text (no fabricated URLs).
- `candidate.payload` (NEW) — per-campaign discovery evidence written
  by `instagram-kol-discovery` into `campaign_candidates.payload_json`.
  Typical keys: `reason`, `niche_match`, `showcase_evidence`,
  `conversion_mechanism`. **Read-only** here — used as a secondary
  source for the "why-them" line when the creator brief is sparse.
  May be `null` when this identity wasn't discovered for this campaign
  (e.g., operator added them manually).
- `identity_facts` (NEW) — all identity-level (`campaign_id IS NULL`)
  facts merged into one dict. Most important keys here:
  `identity.content_pillars`, `identity.signature_hooks`,
  `identity.voice_descriptors`, `identity.hero_post_url`,
  `identity.hero_post_note`, `identity.recommendation_reason`. These
  back the `[P0.1]` creator brief block — when the brief block exists,
  the underlying fact values live here too.

### Step 1b — Resolve the KOL's greeting name (mandatory)

Use this priority order to pick the **first name** for the salutation.
Stop at the first hit:

1. `identity.display_name` is set → take its first whitespace-separated
   token (e.g. `"Becki Owens"` → `Becki`).
2. `reusable_facts['identity.first_name']` is set → use it verbatim.
3. Otherwise, **parse** `identity.primary_handle` into a likely
   `First Last` pair, then take the first token:
   - Strip a leading `@` and any trailing digits/underscores
     (`"beckiowens"`, `"becki_owens_"`, `"beckiowens99"` → `"beckiowens"`).
   - If the handle already contains a separator (`.`, `_`, `-`, space),
     split on it: `"becki_owens"` → `["becki", "owens"]`.
   - Otherwise, attempt a single CamelCase / known-name split. If the
     handle is mixed case (`"BeckiOwens"`), split on the second
     capital. If all-lowercase (`"beckiowens"`), try matching the
     longest common English first-name prefix (Becki, Sarah, Emma,
     Olivia, Alex, Sam, etc.) against a small heuristic — only commit
     to a split when the prefix is **3+ chars** AND the remainder is
     **3+ chars**. Title-case both pieces (`beckiowens` → `Becki
     Owens`). Take the first token (`Becki`).
   - If no split is confident, fall back to the title-cased handle as
     a single token (`"beckiowens"` → `Becki`). Never use the
     full lowercase id as the salutation.
4. If even that fails (numeric-only handle, empty), open an escalation
   `kol_name_unresolvable` with the handle + identity_id and abort.
   Do NOT email someone as `Hi user12345,`.

The greeting MUST be the **first name only** — never include the
last name in `Hi, …`. Correct: `Hi Becki,`. Wrong: `Hi Becki Owens,`,
`Hi beckiowens,`, `Hi @beckiowens,`.

### Step 2 — Compose the email
Constraints:
- Subject: short, brand-name-led, no clickbait. Example:
  "Collab idea from <Brand>" or "<Brand> × <FirstName>". Plain text —
  no HTML in the subject.
- **Body format: HTML.** Wrap paragraphs in `<p>…</p>` and use `<br>`
  for forced line breaks. The product mention MUST be an anchor tag
  when `campaign.product_url` is present:
  `<a href="{campaign.product_url}">{campaign.product_display_name}</a>`.
  Use the URL verbatim from `campaign.product_url`; do NOT fabricate,
  shorten, or rewrap it. Set `html: true` in the draft envelope
  (Step 4) so the bridge marks the Gmail MIME part as `text/html`.
- Body: 3–5 short paragraphs.
  1. One-line greeting + why-them. Use the first name resolved in
     Step 1b: `<p>Hi {FirstName},</p>`. The why-them sentence MUST
     reference **one specific detail** from the `[P0.1]` creator brief
     — either a `content_pillar`, a `signature_hook`, the theme of
     `hero_post` (cite what the post is *about*, not its URL), or the
     `recommendation_reason`. Paraphrase in 1 sentence; do NOT lift
     the brief verbatim and do NOT name-check the creator's own bio.
     If the brief is genuinely sparse, fall back to a detail from
     `candidate.payload.reason` / `candidate.payload.conversion_mechanism`.
     **Only** when `brief_status == "unavailable"` AND `candidate.payload`
     is empty/missing may you write a generic opener — and you MUST
     then flag the draft (see "Personalization envelope flag" below).
  2. Brand one-liner + product hook. Refer to the product **only** via
     the rendered anchor (`<a href="…">{product_display_name}</a>`),
     or — if `product_url` is empty — the plain `product_display_name`.
     If `product_display_name` itself is missing, use a generic
     category phrase (`"our new piece"`, `"a new release"`). Never
     include SKU codes, model numbers, variant IDs, internal catalog
     identifiers, or the `campaign_id` itself in the visible text —
     regardless of whether they appear in `sku_whitelist`, campaign
     label, or anywhere else in the dispatch context. A SKU may
     appear inside the anchor's `href` URL (that is the canonical
     product page), but never as visible anchor text. Do not state
     whether the collaboration is paid, barter/exchange, gifted,
     free-product, or commission-based.
  3. Soft ask: "Would you be open to chatting about a collab?"
     **Do not** ask for shipping, deliverables, or rates yet.
  4. Sign-off (style-loader handles signature in a future phase; for
     now use "Best,<br><operator-name or brand>").
- No emoji, no excessive exclamation marks.
- No "press release" boilerplate.
- Allowed HTML tags: `<p>`, `<br>`, `<a href="…">`, `<strong>`,
  `<em>`. No images, no inline styles, no `<script>`, no tracking
  pixels. Anchor `href` values MUST be `http://` or `https://`
  URLs only.

### Step 2b — SKU leak post-check (mandatory)

After composing the draft and **before** Step 3, scan `subject` and
the **visible text** of `body` against the SKU-leak regex:

```
[A-Z]{2,5}[\- ]?\d{3,5}[A-Z0-9]*
```

Before scanning the body, strip out:
1. Every `href="…"` attribute value (the URL is allowed to contain
   SKU patterns — it points at the canonical product page).
2. Every full HTML tag (so attribute names, classes, etc. don't
   trigger).

What remains is the human-visible text (anchor labels included).
Apply the regex to that residual string and to the `subject`. Also
substring-check the residual string + subject for every entry in
`sku_whitelist` and for the `campaign_id`.

This catches visible leaks like `SEB800`, `SEB-8008`, `TS8319`,
`POV-RUG-04` while permitting them inside `<a href="…">` targets.

If any match is found:
- Do NOT call `write-facts-multi`.
- Do NOT return a draft envelope.
- Abort with:
  ```json
  {"error":"sku_leak_detected",
   "matches":["SEB800", ...],
   "field":"body|subject"}
  ```
The router will surface the failure to the operator (typically a sign
the campaign is missing `product_display_name` or the LLM ignored the
Step 2 constraint — escalate rather than auto-retry).

### Step 2c — Personalization post-check (mandatory)

After the SKU check passes, before Step 3, verify the body actually
incorporates the creator brief.

**When `brief_status ∈ {fresh, refreshed}`:**
1. Build a set of substantive tokens from the creator brief facts:
   - All tokens of length ≥ 4 from `identity.content_pillars[*]`.
   - All tokens of length ≥ 4 from `identity.signature_hooks[*]`.
   - All tokens of length ≥ 5 from `identity.recommendation_reason`
     after stripping the words {`creator`, `content`, `audience`,
     `engagement`, `their`, `your`, `with`, `from`, `that`, `which`,
     `this`}.
2. Strip HTML tags from `body` to get visible text.
3. Case-insensitive substring match: count how many tokens appear in
   the visible body.
4. If **zero tokens match**, the draft did not use the brief. Re-generate
   the draft ONCE with an extra instruction prepended:
   *"Your previous draft did not reference any detail from the [P0.1]
   creator brief. Rewrite the first paragraph after the greeting to
   paraphrase one specific pillar, hook, or hero-post theme from the
   brief — in one sentence, in your own words."*
5. After the retry, re-run the SKU check (Step 2b) AND the personalization
   check. If the retry still has zero matches, abort with:
   ```json
   {"error":"personalization_check_failed",
    "brief_status":"<fresh|refreshed>",
    "tokens_expected":[...],
    "field":"body"}
   ```

**When `brief_status == "unavailable"`:**
Skip the token check. The draft is allowed to be generic, but Step 4
(envelope) MUST include `low_personalization: true` and
`low_personalization_reason: "creator_brief_unavailable"` so the
operator review surface flags the draft for manual personalization.

### Step 3 — Write outbound facts (single call)
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-cold-outreach",
            "namespaces":{
              "offer":    {"offer.outreach_draft_ready": true,
                            "offer.outreach_path": "cold"},
              "identity": {"identity.last_outreach_draft_at": "<iso8601>"}
            }}'
```

If the call returns a `FactNamespaceError`, abort and return
`{"error":"fact_namespace_violation","details":"..."}`. Do NOT retry
with munged keys.

### Step 4 — Return draft envelope
Final assistant message must be a single JSON object — no prose,
no markdown:

```json
{
  "skill": "kol-cold-outreach",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "subject": "Collab idea from POVISON",
  "body": "<p>Hi Becki,</p><p>...<a href=\"https://povison.com/products/atlas-sofa\">the POVISON Atlas sofa</a>...</p>",
  "html": true,
  "to": "<resolved from identity.primary_email>",
  "thread_id": null,
  "facts_written": {"offer": 2, "identity": 1},
  "brief_status": "fresh",
  "personalization_tokens_matched": ["cozy hosting", "honest reviews"]
}
```

`html: true` is mandatory whenever the body contains anchor tags so
the bridge sends the Gmail draft with a `text/html` MIME part.

`brief_status` mirrors the value from the `[P0.1]` creator brief block.
`personalization_tokens_matched` lists the brief tokens Step 2c found in
the body — empty list is only allowed when `brief_status == "unavailable"`.

When `brief_status == "unavailable"`, the envelope MUST instead include
`low_personalization: true` and `low_personalization_reason:
"creator_brief_unavailable"` (in lieu of `personalization_tokens_matched`)
so the operator review queue can surface these drafts for manual edits.

The caller (router or operator) is responsible for persisting the
draft to Gmail.

## Examples

### Success
Brand-new KOL `@alice` selected for outreach. Step 1 confirms
`outreach.status=active`, `total_collabs=0`. Step 2 composes a 4-para
open-ended collab email. Step 3 writes 2 draft-ready offer facts + 1
identity fact in one call. Step 4 returns
`{subject, body, to, thread_id: null, ...}`.

### Failure — wrong path
Step 1 reveals `total_collabs=3`. Skill aborts with
`{"skipped":"not_a_new_prospect","delegate_to":"kol-reengagement-outreach"}`.

### Failure — already sent
Step 1 reveals `outreach.status=satisfied`. Skill aborts with
`{"skipped":"already_sent"}`. The router will not re-trigger.

## Pitfalls
- Never paste a SKU / model code / `campaign_id` into the email body
  or subject — even if that's the only product identifier the campaign
  context carries. Use `campaign.product_display_name`; if absent,
  fall back to a generic category phrase. The Step 2b regex guard is
  a backstop, not a license to skip the constraint.
- Never lift the creator brief verbatim into the body — paraphrase in
  your own words. A copy-pasted pillar phrase reads exactly as canned
  as a generic opener. Step 2c only checks that a brief token appears
  somewhere in the body, not that the sentence sounds human; you still
  have to write a real sentence.
- Never set `brief_status: "unavailable"` without a real loader failure.
  If the loader returned `fresh`/`refreshed`, the brief exists and you
  must use it — falsely flagging `unavailable` to skip Step 2c will
  surface in operator review as a low-quality draft.
- Never greet the KOL by their last name or full raw handle.
  `Hi beckiowens,` / `Hi @beckiowens,` / `Hi Becki Owens,` are all
  wrong; use `Hi Becki,` (first name only, resolved via Step 1b).
- Never invent a `product_url`. If `campaign.product_url` is empty,
  render the product name as plain text — do not link it to
  `campaign_id`, the brand homepage, or a guessed product page.
- Never set `html: false` while keeping anchor tags in `body`. The
  draft will be sent as `text/plain` and the KOL will see raw
  `<a href="…">…</a>` markup.
- Never insert price / commission / deliverable count language in the
  cold email — those goals are downstream and the dispatcher will pick
  them up after the reply.
- Never reference prior collab history in a cold email; that's the
  reengagement skill's job. Mixing tones leaks data and confuses the
  KOL.
- Do not call `cal.py` / direct SQL / `execute_code`. The single
  `write-facts-multi` call is the entire write surface.
- The skill is a draft generator only — sending is a separate
  concern. Do not attempt to invoke Gmail APIs from here.
