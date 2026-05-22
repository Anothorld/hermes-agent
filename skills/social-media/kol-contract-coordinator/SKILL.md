---
name: kol-contract-coordinator
description: Handles the contract phase after compensation is agreed. Three branches — (1) initiate: assemble fields, render the POVISON template into a docx, attach to a reply draft, and write `offer.contract_sent=true`; (2) chase: nudge a KOL who hasn't signed within the configured follow-up window; (3) handle response: when classifier extracts `offer.contract_signed=true` (acknowledged), no draft needed — just write the fact; when KOL asks to change a CORE clause (exclusivity, IP, payment terms), open escalation and write `approval.contract_change_request`. Skipped automatically when `campaign_config.contract_required=false`.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.commerce == "contract_signing"`. Also invoked by a future cron-driven follow-up loop for chase mode (out of scope this phase). Never invoked when `goals.contract_signing.status == "skipped"` (config says no contract).
tags: ["kol", "contract", "draft-generator", "approval", "commerce-lane"]
---

## Goal
Drive the contract sub-goal from `agreed → sent → signed` (or to
`escalation` when KOL pushes back on core clauses), without ever
authoring legally-binding language ourselves. Branch I now produces
a contract docx by mechanically filling the legal-approved POVISON
template; the agent is the **renderer**, not the **author**.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Never invent or rewrite contract clauses.** Branch I renders the
  fixed template at `plugins/kol-ops-bridge/templates/povison_agreement.docx`
  with field substitutions only. If a required field is missing
  (see Step I.1) → escalate, do not improvise.
- **Core-clause changes are always escalation.** Anything touching
  exclusivity, IP/usage, payment terms, term length, governing law
  → escalation, not negotiation. Cosmetic edits (typos, name
  spelling, address) → write `approval.contract_change_request`
  with `severity=low` and proceed.
- **Skip when not required.** If `goals.contract_signing.status == "skipped"`,
  abort `{"skipped":"contract_not_required"}`.
- **Idempotent.** If `goals.contract_signing.status == "satisfied"`,
  abort `{"skipped":"already_signed"}`.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: one of `initiate | chase | handle_response`.
3. `inbound_excerpt` (only for `handle_response`).
4. Classifier-extracted facts:
   `offer.contract_signed_signal` (`signed | declined | change_requested | silent`),
   `offer.contract_change_kind` (`core | cosmetic | null`).

## Email Style Preamble (mandatory before drafting)

Before composing any draft, this skill **MUST** invoke
`kol-email-style-loader` and prepend its output verbatim to the LLM
prompt. **P0 (goal / required facts) > P1 (company style) > P2 (personal style)**.

Call contract:
- inputs: `goal_brief = {goal: "contract_signing", missing_facts: [<from goal_state>], next_action: "<send | chase | acknowledge change request>"}`,
  `current_user_id = <operator id from session>`.
- output: prepend as the first section of the draft prompt.
- failure mode: empty-doc fallbacks; never block.

>>> include: kol-email-style-loader

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `goals.compensation_negotiation.status` — must be `satisfied`
  (defense-in-depth; aborts otherwise).
- `goals.contract_signing.status`.
- `campaign_config.contract_required` — must be `true`.
- Latest `offer.compensation_mode`, `offer.agreed_terms` — for
  templating the email reference.

### Step 2 — Branch on `mode`

**Branch I — initiate:**

#### Step I.1 — Assemble contract fields
Build a JSON dict with the following shape. Pull from
dispatch-context first; for anything missing, scan the email thread
(via the inbound excerpts and prior outbound drafts already loaded
in context) for confirmed values. Do **not** invent values.

```jsonc
{
  "date": "<YYYY-MM-DD today, in operator's calendar>",
  "influencer": {
    "full_name": "<identity.full_name>",            // REQUIRED
    "email":     "<identity.primary_email>",        // REQUIRED
    "phone":     "<identity.phone or '' if absent>",
    "address":   "<identity.default_shipping_address or '' if absent>",
    "instagram": "<identity.social_links.instagram or ''>",
    "tiktok":    "<identity.social_links.tiktok or ''>",
    "youtube":   "<identity.social_links.youtube or ''>"
  },
  "product": {
    "specs": "<campaign_config.product_specs — short product name + variant + SKU>",  // REQUIRED
    "link":  "<campaign_config.product_link>"                                          // REQUIRED
  },
  "fee": <null when compensation_mode == "free_product"
          | {"amount":"<integer>", "currency":"USD"} when offer.agreed_terms includes a flat fee>,
  "deliverables": [                                                                    // REQUIRED, len >= 1
    {
      "type":                  "<e.g. 'Short Video + IG Stories + RAW'>",
      "description":           "<e.g. 'Showcase product per brief'>",
      "quantity":              "<e.g. '1 video, 3 stories, RAW footage'>",
      "requirements":          "<e.g. '20-60s vertical, English VO/captions'>",
      "time_of_uploading":     "<e.g. 'Within 2 weeks of receiving product'>",
      "platform_of_uploading": "<e.g. 'IG Collab + TikTok + YT Shorts'>"
    }
    // append additional rows (Ad Codes, BTS, etc.) if campaign asks for them
  ]
}
```

**Escalate (do NOT render)** when any REQUIRED field is missing or
when `compensation_mode` is `cash` but `offer.agreed_terms` doesn't
yield a numeric fee. Open escalation with
`reason="contract_fields_incomplete: <field list>"` and skip the
remaining steps.

#### Step I.2 — Render the contract docx
Run the renderer with the JSON from I.1 piped on stdin. Output path
lives under HERMES_HOME so it's per-profile.

```bash
OUT="${HERMES_HOME:-$HOME/.hermes}/kol-ops-bridge/contracts/<env>/<campaign_id>/<identity_id>_$(date +%Y%m%d).docx"
mkdir -p "$(dirname "$OUT")"
echo '<fields-json>' | python plugins/kol-ops-bridge/scripts/render_contract.py \
  --template plugins/kol-ops-bridge/templates/povison_agreement.docx \
  --output   "$OUT" \
  --fields   -
```

The script prints the absolute output path to stdout; capture it as
`contract_path`. If rendering exits non-zero, treat as
escalation (`reason="contract_render_failed: <stderr>"`) — do
**not** send a draft without an attachment.

#### Step I.3 — Compose the email body
Body skeleton (style preamble from the loader still applies):
> "Great, glad we're aligned on the terms. Attached is our standard
> agreement reflecting what we discussed (`<one-line summary of
> agreed terms>`). Have a look and reply with your signed copy at
> your convenience — happy to walk through anything that needs
> clarifying."

Write:
```
write-facts-multi --json '{
  "campaign_id":"...","source":"skill:kol-contract-coordinator",
  "namespaces":{
    "offer":{"offer.contract_sent": true,
              "offer.contract_sent_at": "<iso8601>",
              "offer.contract_artifact_path": "<contract_path>"}
  }
}'
```

**Branch C — chase:**
Body skeleton:
> "Just bumping this — let me know if anything in the agreement is
> blocking. Happy to walk through any clause."

Do NOT write a fact for chase (no state change); the
`offer.contract_chase_count` could be incremented in a future
extension (deferred — not in this phase).

**Branch R — handle_response:**

| `contract_signed_signal` | `contract_change_kind` | Action |
|---|---|---|
| `signed` | n/a | write `offer.contract_signed=true` + `offer.contract_signed_at`; no draft, return `{"acknowledged_only": true}` |
| `declined` | n/a | open escalation `goal=contract_signing reason="KOL declined to sign"` + write `offer.contract_declined_reason=<excerpt>` |
| `change_requested` | `core` | open escalation `goal=contract_signing reason="KOL requested core-clause change: <excerpt>"` + write `approval.contract_change_request={"kind":"core","excerpt":"...","decision":"pending"}` |
| `change_requested` | `cosmetic` | write `approval.contract_change_request={"kind":"cosmetic","excerpt":"...","decision":"pending"}` (severity low — operator approves async); draft a holding reply: "noted, will get the cosmetic update in" |

When opening escalation, omit Step 4 facts that conflict (e.g. don't
mark `contract_sent` when handling response).

### Step 3 — (Branch I/C only) Compose the email
Branch I/C produce a body. Branches that escalate or just
acknowledge return `body: null`.

### Step 4 — Write fact (per Step 2 table)
Each row prescribes its own fact set; emit one
`write-facts-multi` call per row, atomic.

### Step 5 — Return draft envelope
```json
{
  "skill": "kol-contract-coordinator",
  "mode": "initiate | chase | handle_response",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "body": "<reply or null>",
  "attachments": ["<absolute path to rendered docx, or omitted>"],
  "branch_action": "drafted | escalated | acknowledged_only | cosmetic_pending_approval",
  "facts_written": {"offer": 1, "approval": 1},
  "escalation_opened": false
}
```

Do **not** set `to` or `subject` — the dispatcher fills these from the
inbound message before persisting `approval.reply_draft`.
`attachments` is REQUIRED for Branch I and must contain exactly the
`contract_path` from Step I.2. For other branches it is omitted or
`[]`. The downstream `approval.reply_draft.draft.attachments` field
is consumed verbatim by the bridge's Gmail wrapper, which validates
each path exists before creating the draft.

## Examples

### Branch I
KOL just agreed on $1050 flat; classifier said
`compensation_negotiation` advanced to satisfied. Coordinator
assembles fields (full name, email, deliverables from campaign,
`fee={amount:1050,currency:USD}`), renders the POVISON template
into `~/.hermes/kol-ops-bridge/contracts/LIVE/TS8319/42_20260522.docx`,
drafts "Attached is our standard agreement...", returns an envelope
with that path under `attachments`, and writes
`offer.contract_sent=true` + `offer.contract_artifact_path=<path>`.

### Branch R — core change
Inbound: "Looks good, but can we cap exclusivity at 30 days?"
Classifier extracts `change_kind=core`. Coordinator opens
escalation + writes `approval.contract_change_request.kind=core`.

### Branch R — cosmetic
Inbound: "All good — please change my legal name to <X>."
Classifier extracts `change_kind=cosmetic`. Coordinator writes
`approval.contract_change_request.kind=cosmetic` + drafts holding
reply. Operator approves later via ApprovalsPage.

### Skipped
`campaign_config.contract_required=false`. Skill aborts
`{"skipped":"contract_not_required"}`.

## Pitfalls
- **Drafting actual clause language.** Always defer to the rendered
  template; never rewrite a sentence inside it. If a field is
  unknown, escalate — leaving a `${...}` placeholder unfilled is
  blocked by the renderer (it strips unmapped tokens), so a missing
  fact silently becomes an empty cell unless you check first.
- **Forgetting `attachments`.** Branch I envelopes without an
  `attachments` entry will be sent as a clauseless "here's the
  agreement" body with nothing attached. The bridge does NOT
  re-attach for you.
- Treating cosmetic vs core changes the same way. Cosmetic changes
  don't deserve an escalation — they create approval entries
  instead.
- Marking `contract_signed=true` on the basis of "I'm in!" alone —
  must come from classifier-confirmed signed signal (e.g. a
  DocuSign completion email or explicit "I've signed and returned").
- Forgetting to mark the `*_at` timestamp; downstream cron uses it
  to compute follow-up windows.
