Single-identity campaign_redraft_outreach flow

When the operator triggers a redraft for one specific identity within an existing campaign, keep the run strictly scoped to that identity_id.

Observed contract from session:
- First verify get-dispatch-context for the exact identity_id + campaign_id pair.
- Stop immediately if the returned candidate does not belong to that campaign context.
- If primary_email already exists on get-identity, skip kol-email-discovery entirely.
- Determine path from CAL relationship/reusable facts:
  - total_collabs = 0 or relationship_status = new_prospect -> cold
  - repeat relationship -> reengagement
- Regenerate the draft but do NOT send mail and do NOT set offer.outreach_sent=true.
- Persist both:
  1. write-event event_type=kol_initial_outreach_draft_ready
  2. approval.reply_draft with decision=pending and kind=initial_outreach
- If a prior approval.reply_draft exists, overwrite it with the new draft payload.

CLI details worth preserving:
- kol_bridge_tool.py write-event expects --json JSON_OR_@PATH, not --payload-json.
- kol_bridge_tool.py write-facts-multi and write-event both accept @/tmp/file.json payloads reliably.
- For nested draft/event payloads, prefer temp-file-backed @path JSON instead of inline shell quoting.

Suggested draft payload shape for approval.reply_draft:
- decision: pending
- kind: initial_outreach
- child_skill: kol-cold-outreach or kol-reengagement-outreach
- draft:
  - skill
  - identity_id
  - campaign_id
  - env
  - subject
  - body
  - html
  - to
  - thread_id
  - brief_status
  - personalization tokens or low_personalization flags

Evidence used in the session to support personalization refresh:
- IG profile bio exposed DIY + home positioning and family/home-building angle.
- Reel evidence showed value-led furniture storytelling and explicit product reaction language.

This is a support note for future redraft runs; keep the umbrella logic in SKILL.md, and use @path JSON payload files for all nested bridge writes in this flow.
