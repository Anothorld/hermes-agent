---
name: kol-outreach-content-review
description: Process an inbound KOL email that the reply dispatcher classified as a content_submission (KOL has delivered the draft video). Extracts the video URL via a multi-platform whitelist, advances the KOL card to content_delivery.submitted, and surfaces the link to the human operator in the Web console. Does NOT auto-approve and does NOT draft any reply email — the operator decides approve/revise in the Web console.
trigger: When the kol-reply-dispatcher detects intent=content_submission on an inbound reply for a KOL whose current stage is logistics.delivered or later. Not for any other intent.
tags: ["kol", "outreach", "content", "review", "video"]
---

## Goal
Extract the submitted video URL from a KOL reply, record it as `content_delivery.submitted_v<n>` in CAL, and notify the operator. Approval / revision is a human decision pushed back from the Web console.

## Inputs (from caller)
- `campaign_id`, `kol_handle`, `kol_identity_id` (CAL's `card_id` column is legacy — pass NULL)
- The Gmail `message_id`, `thread_id`, `from_addr`, `body`, `received_at` of the inbound reply
- `triggered_by` (always `cron` for dispatcher-routed invocations; `chat` for manual replays)
- `env` (`TEST` | `LIVE`)

Fail loudly if any required input is missing.

## Procedure

### Step 1 — Verify preconditions
1. Confirm the current KOL stage is `logistics.delivered` or `content_delivery.*`. If earlier, refuse and ask the dispatcher to re-route (likely a misclassification).
2. Determine the next submission `version`: previous `content_delivery.submitted_v<n>` events count + 1.

### Step 2 — Extract the video URL
Use the multi-platform whitelist below. Scan the email body for URLs whose host matches (case-insensitive, www. stripped):

| Platform | Hosts |
|---|---|
| YouTube | `youtube.com`, `youtu.be` |
| TikTok | `tiktok.com`, `vm.tiktok.com` |
| Instagram | `instagram.com/reel`, `instagram.com/p` |
| Bilibili | `bilibili.com`, `b23.tv` |
| Vimeo | `vimeo.com` |

Rules:
- Keep only the **first** URL whose host matches the whitelist. If the URL is a known shortener (`youtu.be`, `vm.tiktok.com`, `b23.tv`), record both the short form (as `submitted_url`) and a `submitted_url_resolved` if a HEAD-resolution helper is available. If resolution fails, do not block — just record the short form.
- If no whitelisted URL is found, do NOT advance the stage. Instead record a CAL event `content_submission_no_url` and escalate.
- Reject any URL whose host is on the campaign's `blocked_hosts` list (config-driven).

### Step 3 — Advance state via CAL
No Kanban card to update. Persist the submission inline in the Step 4 CAL event (`event_type='content_submitted'`, `stage='content_delivery'`, `sub_status='submitted_v<n>'`) and include the full submission record in its `payload`:

```json
{
  "version": <n>,
  "video_url": "<url>",
  "submitted_url_resolved": "<resolved or null>",
  "received_at": "<iso8601>",
  "gmail_message_id": "<id>",
  "gmail_thread_id": "<id>",
  "from_addr": "<addr>"
}
```

Each submission is its own append-only event in `kol_conversation_events`; the operator reads the full submission history by querying CAL for `event_type='content_submitted'` rows for this `kol_identity_id`.

### Step 4 — Write CAL audit
Call `cal.record_event` with:
- `event_type: content_submitted`
- `stage: content_delivery`, `sub_status: submitted_v<n>`
- `payload: {version, video_url, gmail_message_id, gmail_thread_id, from_addr}`

### Step 5 — Notify operator
Post a single notification:
- "Content submitted by @<handle> (v<n>) — review in console."
- Deep link to the KOL detail page (the Web operator sees an embedded preview and the approve/revise buttons there).

### Step 6 — Return
Return `{video_url, version, kol_handle}` to the caller. Do NOT draft any email; the operator decides next steps.

## Hard Rules
- Never auto-approve a video. Approval is human-only.
- Never draft a reply email here. The follow-up draft for "revise" is the job of `kol-outreach-content-revision`, fired by the Web console.
- Never advance to `closed` here. Closing is the orchestrator's job after `content_approved`.
- Never accept an URL outside the platform whitelist — that ambiguity must escalate to the operator.

## Pitfalls
- Do not strip query strings from video URLs (`?t=` time markers and `?si=` share ids are sometimes load-bearing); record the URL verbatim.
- Do not assume the email contains exactly one URL; KOLs often paste the link twice (reel + watermark page). Take the first whitelisted match.
- Do not require the URL to be live (HEAD 200) before advancing — many platforms require auth and would fail probes.
