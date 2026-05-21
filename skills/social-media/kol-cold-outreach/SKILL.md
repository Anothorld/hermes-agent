---
name: kol-cold-outreach
description: Generates the FIRST outreach email to a brand-new KOL ("new_prospect" path). Reads campaign config + identity facts via the Bridge dispatch-context, composes a concise barter-first opening (no price, no contract talk), writes outbound facts (`offer.outreach_sent=true`, `offer.outreach_path=cold`) via the Bridge, and returns the draft envelope as JSON for the caller to persist. Never sends mail directly. Does not handle replies — the next inbound flows back through `kol-reply-dispatcher`.
trigger: Invoked by `kol-discovery-to-outreach-router` for candidates assigned `identity.outreach_path=cold` (i.e. `relationship_status=new_prospect` who were just selected for outreach), or on demand when the user says "draft a cold outreach to <handle>". Never auto-runs from a cron — only chained.
tags: ["kol", "outreach", "cold", "first-contact", "draft-generator", "commerce-lane"]
---

## Goal
Produce one well-targeted opening email to a new KOL — barter-first,
brand-introduction tone, no quotes, no contract terms — and atomically
record on the Bridge that we've reached out. No reply handling, no
threading state, no Gmail send.

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
  `{"skipped": "already_sent"}`; do **not** redraft.
- **No price talk:** never mention compensation, paid rate, commission
  percentage, or contract terms. The opening offers product collab
  (gifted-first); price is owned by `kol-compensation-negotiator`.
- **No deliverable counts:** never commit deliverable counts or
  platforms in this email; deliverables are framed only as "we'd love
  to discuss together". `kol-deliverables-clarifier` owns that
  conversation.

## Inputs
1. `identity_id` (mandatory).
2. `campaign_id` (mandatory).
3. `env` (`TEST` or `LIVE`, mandatory).

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

### Step 2 — Compose the email
Constraints:
- Subject: short, brand-name-led, no clickbait. Example:
  "Collab idea from <Brand>" or "<Brand> × @<handle>".
- Body: 3–5 short paragraphs.
  1. One-line greeting + why-them (cite one specific recent post if
     classifier or operator supplied it; otherwise generic).
  2. Brand one-liner + product hook (gifted product first; never
     mention paid).
  3. Soft ask: "Would you be open to chatting about a collab?"
     **Do not** ask for shipping, deliverables, or rates yet.
  4. Sign-off (style-loader handles signature in a future phase; for
     now use "Best, <operator-name or brand>").
- No emoji, no excessive exclamation marks.
- No "press release" boilerplate.

### Step 3 — Write outbound facts (single call)
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <identity_id> --env <TEST|LIVE> \
  --json '{"campaign_id":"<campaign_id>",
            "source":"skill:kol-cold-outreach",
            "namespaces":{
              "offer":    {"offer.outreach_sent": true,
                            "offer.outreach_path": "cold"},
              "identity": {"identity.last_outreach_at": "<iso8601>"}
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
  "body": "Hi @alice, ...",
  "to": "<resolved from identity.primary_email>",
  "thread_id": null,
  "facts_written": {"offer": 2, "identity": 1}
}
```

The caller (router or operator) is responsible for persisting the
draft to Gmail.

## Examples

### Success
Brand-new KOL `@alice` selected for outreach. Step 1 confirms
`outreach.status=active`, `total_collabs=0`. Step 2 composes a 4-para
barter-first email. Step 3 writes 2 offer facts + 1 identity fact in
one call. Step 4 returns `{subject, body, to, thread_id: null, ...}`.

### Failure — wrong path
Step 1 reveals `total_collabs=3`. Skill aborts with
`{"skipped":"not_a_new_prospect","delegate_to":"kol-reengagement-outreach"}`.

### Failure — already sent
Step 1 reveals `outreach.status=satisfied`. Skill aborts with
`{"skipped":"already_sent"}`. The router will not re-trigger.

## Pitfalls
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
