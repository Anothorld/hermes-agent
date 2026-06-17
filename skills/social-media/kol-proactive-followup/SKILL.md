---
name: kol-proactive-followup
description: Operator-topic follow-up draft in existing thread.
tags: ["kol", "followup", "draft-generator", "operator-driven"]
---

# kol-proactive-followup Skill

Operator-initiated follow-up email in an **existing** Gmail thread (not
the inbound-reply dispatcher loop). Reads the same CAL context as reply
drafting, writes `approval.reply_draft` with `kind=proactive_followup`.
Never sends mail or creates Gmail drafts directly.

## When to Use

- Console `POST .../followup-draft` with `operator_topic` in the brief.
- Refine of a pending `approval.reply_draft` whose `child_skill` is
  `kol-proactive-followup` (use `operator_topic` + `operator_refinement_prompt`;
  there may be no `kol_inbound_reply` event).

## Prerequisites

- `offer.outreach_sent=true` (initial touch already sent).
- `identity.primary_email` present (or TEST `campaign_config.test_mode_to`).
- Real `thread_id` on the draft envelope (from facts/timeline) so approve
  creates a **reply draft in the existing thread** (Re: subject, Gmail
  quote block) — never a standalone new email.

## Inputs

1. `identity_id`, `campaign_id`, `env` (`TEST|LIVE`) — mandatory.
2. `operator_topic` — operator intent (e.g. chase timeline, confirm shoot).
3. `operator_refinement_prompt` — optional; refine runs only.
4. `thread_history` — optional JSON array `{from, date, body}` from timeline.

## Shared Blocks

- Runtime/draft guardrails:
  `../kol-interest-qualifier/references/shared/runtime-draft-guardrails.md`
- Style preamble:
  `../kol-interest-qualifier/references/shared/style-and-brief-preambles.md`
- Learning hints:
  `../kol-reply-dispatcher/references/shared/learning-hints.md`

## Procedure

### Step 1 — Load context

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <id> --campaign-id <cid> --env <env> --view agent

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-timeline \
  --identity-id <id> --campaign-id <cid> --env <env> --limit 100

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-identity \
  --identity-id <id>
```

From timeline (newest first), collect:

- `thread_id` — prefer `offer.gmail_thread_id` from dispatch-context facts,
  else latest event payload `thread_id`.
- `source_message_id` — latest `kol_inbound_reply.payload.message_id` if any;
  else synthetic `proactive-followup:<env>:<identity_id>:<unix_epoch>`.
- `latest_email` — latest inbound payload if present; else latest outbound
  (`kol_outreach_sent` / similar) for `persist-reply-draft` enrichment only.

Abort with structured error if no `thread_id` and no email to send to.

### Step 2 — Style + learning (mandatory before drafting)

>>> include: kol-email-style-loader

Apply `learning_hints` per
`../kol-reply-dispatcher/references/shared/learning-hints.md` (advisory only;
Hindsight recall is below learning_hints — see memory layers there).

Prepend `[P0.3] Conversation history` from timeline (oldest → newest) when
`thread_history` is provided; same rules as interest-qualifier (no re-ask
answered questions; vary phrasing).

### Step 3 — Draft envelope

Write ONE follow-up that advances `operator_topic` (and refinement prompt
when present). Output **full envelope**:

```json
{
  "to": "<primary_email or test_mode_to in TEST>",
  "subject": "Re: <prior subject>",
  "body": "<new prose only — no quoted block; approve adds Gmail quote>",
  "thread_id": "<real Gmail thread id>",
  "kind": "proactive_followup",
  "operator_topic": "<verbatim operator topic>"
}
```

Hard rules:

- Do **not** embed `On … wrote:` or `>` quote lines in `body` (bridge adds
  on approve when missing).
- Do **not** set `cc` (bridge computes reply-all Cc on approve).
- Do **not** invent prices, SKUs, or commitments not in facts.
- Do **not** write `offer.outreach_sent` or domain facts unless explicitly
  instructed by a refine that only touches copy (default: content-only).

### Step 4 — Persist

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py persist-reply-draft \
  --identity-id <id> --campaign-id <cid> --env <env> \
  --json @/tmp/persist.json
```

`persist.json` shape (`PersistReplyDraftBody`):

```json
{
  "identity_id": 0,
  "campaign_id": "",
  "env": "TEST",
  "source_message_id": "<from Step 1>",
  "primary_lane": "meta",
  "primary_goal": "proactive_followup",
  "child_skill": "kol-proactive-followup",
  "child_envelope": { "to": "", "subject": "", "body": "", "thread_id": "", "kind": "proactive_followup", "operator_topic": "" },
  "latest_email": { "from": "", "subject": "", "thread_id": "" }
}
```

Merge `operator_topic` into the approval fact by re-reading after persist;
if the bridge fact schema omits it, also:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts-multi \
  --identity-id <id> --campaign-id <cid> --env <env> \
  --json '{"namespaces":{"approval":{"approval.reply_draft":"<merged prior value with operator_topic in draft object>"}}}}'
```

Prefer embedding `operator_topic` inside `child_envelope` and the stored
`draft` object so refine can recover it.

Overwrite any prior `approval.reply_draft` for this (identity, campaign)
when the operator explicitly requested a new follow-up draft.

## Examples

### Success — chase timeline

Topic: "催一下对方确认拍摄时间". Timeline shows prior outreach sent,
no KOL reply. Draft: short friendly nudge referencing product name from
`campaign_config`, `thread_id` set, `decision=pending` after persist.

### Failure — no thread

`offer.outreach_sent=true` but no `thread_id` in facts or timeline.
Return `{"error":"no_thread_id","hint":"cannot attach follow-up to Gmail thread"}`.

## Pitfalls

- Synthetic `source_message_id` without `thread_id` breaks approve-time
  reply-all / quote — always resolve thread first.
- Copying cold-outreach tone — this is a **follow-up** in an open thread.
- Putting quoted prior mail in `body` — duplicates bridge quoting on approve.

## Verification

Re-read `approval.reply_draft`: `decision=pending`, `child_skill` =
`kol-proactive-followup`, non-empty `draft.body`, `draft.thread_id` set.
