---
name: kol-golive-and-boost
description: After draft approval, drives the post live and boost coordination — confirms posting time, sends final hashtag/mention/handle bundle, optionally requests a no-watermark asset for paid boosting, and provides the boost authorization code if the campaign uses paid amplification. Three modes — (1) prep: send go-live bundle with handles/hashtags + posting-time ask; (2) handle_response: KOL confirms posting time → write `publish.golive_at_planned`; KOL provides post URL → write `publish.posted_url` + `publish.posted_at` + `publish.golive_done=true`; (3) boost_followup: ask for whitelisted boost code or no-watermark asset post-publish.
trigger: Invoked by `kol-reply-dispatcher` when `active_goals_by_lane.publish == "golive"` AND `publish.draft_approved == true`. `boost_followup` mode runs after `publish.golive_done == true` AND `campaign_config.boost_required == true` AND `publish.boost_handoff_done != true`.
tags: ["kol", "golive", "boost", "draft-generator", "publish-lane"]
---

## Goal
Get the post live with the right handles/hashtags, capture the live
URL, and (if applicable) close the boost handoff loop.

## Runtime Contract
- Profile: `outreach-operator`. `--env <TEST|LIVE>` mandatory.
- **Boost code never echoes secrets.** If
  `campaign_config.boost_meta_partner_code` looks like a token (long
  alphanumeric, contains `_token_` / `secret`), abort
  `{"error":"unsafe_boost_code_payload"}` and escalate. Codes meant
  for KOLs are short shareable handles, not raw API tokens.
- **One handle/hashtag bundle per campaign.** Read from
  `campaign_config.required_mentions` and
  `campaign_config.required_hashtags`. Don't invent.
- **Idempotent guards** on each mode.
- **Posted URL must look like a real post URL** (regex on host) before
  marking `golive_done=true`; else write nothing and ask for a clean
  link.

## Inputs
1. `identity_id`, `campaign_id`, `env`, `thread_id`.
2. `mode`: `prep | handle_response | boost_followup`.
3. For `handle_response`: classifier-extracted
   `publish.posted_url_proposed` and/or `publish.golive_at_proposed`.

## Procedure

### Step 1 — Load context
```
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-dispatch-context \
  --identity-id <identity_id> --campaign-id "<campaign_id>" --env <TEST|LIVE>
```
Read:
- `campaign_config.required_mentions`, `required_hashtags`,
  `boost_required`, `boost_meta_partner_code`, `no_watermark_required`.
- Latest `publish.draft_approved` / `publish.golive_done` /
  `publish.boost_handoff_done`.

### Step 2 — Branch on mode

**Mode PREP:**
- Idempotent guard: if `publish.golive_done==true`, skip to
  `boost_followup` mode (or abort).
- Body skeleton:
  > "All set on our end! When posting, please make sure to:
  > - tag: `<required_mentions joined with " ">`
  > - include: `<required_hashtags joined with " ">`
  > `<if no_watermark_required: "- if you can share the no-watermark
  > version after posting, that helps us with paid amplification">`.
  >
  > What's the planned go-live time? Once it's up, drop the link here
  > and we're done."
- Write `publish.golive_bundle_sent=true` + `publish.golive_bundle_sent_at`.

**Mode HANDLE_RESPONSE:**

| Signal | Action |
|---|---|
| `golive_at_proposed` only (e.g. "going live tomorrow 7pm PT") | write `publish.golive_at_planned=<iso8601>`; draft "Sounds good — talk after it's up." |
| `posted_url_proposed` validates as URL (host matches platform) | write `publish.posted_url=<url>` + `publish.posted_at=<iso8601>` + `publish.golive_done=true`; draft "Got it, looks great live! Sharing internally." If `boost_required==true`, append "I'll follow up with one more ask re: paid amplification." |
| `posted_url_proposed` invalid | write nothing; reply "Could you re-share the link? It came through truncated." |
| both | write all three + draft combined ack |

**Mode BOOST_FOLLOWUP:**
- Idempotent guard: if `publish.boost_handoff_done==true`, abort.
- If `boost_meta_partner_code` is configured AND passes safety check:
  > "For paid amplification, please grant our partner code on Meta:
  > `<boost_meta_partner_code>`. Quick guide: `<doc link if any>`."
- If `no_watermark_required==true` AND `publish.no_watermark_url`
  not yet written: append "Could you also share the no-watermark
  version? File or link both work."
- Write `publish.boost_handoff_done=true` only when ALL required
  signals from KOL are captured (boost_code_acknowledged AND
  no_watermark_url, depending on config). Otherwise just
  `publish.boost_followup_sent=true`.

### Step 3 — Return draft envelope
```json
{
  "skill": "kol-golive-and-boost",
  "mode": "prep | handle_response | boost_followup",
  "branch_action": "bundle_sent | golive_time_recorded | golive_done | boost_handoff_sent | boost_handoff_done | url_invalid_retry",
  "identity_id": 42,
  "campaign_id": "TS8319",
  "env": "TEST",
  "thread_id": "...",
  "subject": null,
  "body": "<reply or null>",
  "facts_written": {"publish": <n>}
}
```

## Examples

### Prep
`required_mentions=["@povison"]`, `required_hashtags=["#povisonrugs"]`,
`no_watermark_required=true`. Skill drafts the 3-line ask, writes
`golive_bundle_sent=true`.

### Live URL captured
KOL replies "live: https://www.instagram.com/p/Cabc123/". URL host
matches `instagram.com`. Skill writes `posted_url`, `posted_at`,
`golive_done=true`. If `boost_required`, body appends boost teaser.

### Boost handoff
`boost_required=true`, `boost_meta_partner_code="POV-PARTNER-2026"`
(short, safe). Skill drafts boost ask, writes `boost_followup_sent=true`.
Once KOL replies confirming, next pass writes `boost_handoff_done=true`.

### Unsafe boost code
`boost_meta_partner_code="meta_partner_secret_token_a8f3..."` — looks
like a raw token. Skill aborts and escalates instead of leaking.

## Pitfalls
- Marking `golive_done=true` from a KOL's verbal "it's posted" without
  a URL. Always require a real URL.
- Putting hashtags/mentions in two messages (e.g. once at brief, once
  at golive). Brief gives content guidance; golive gives final mention
  bundle. Don't double-source.
- Sending the boost code before `golive_done`. Wait until the post is
  actually live.
- Treating no-watermark as "nice to have" when
  `no_watermark_required==true`. It's a campaign requirement; chase it.
