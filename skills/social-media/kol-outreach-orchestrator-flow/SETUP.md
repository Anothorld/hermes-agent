# KOL Outreach — Operational Setup

One-time manual setup before running the orchestrator. All on-going work
(discovery, drafts, escalation) is handled by the skills themselves.

## 1. Gmail labels (manual, in Gmail UI or via API)

Create these nested labels under the user's outreach mailbox:

```
kol-outreach/
├── pending/
│   ├── initial            # drafts produced by kol-outreach-initial-email
│   ├── product_pitch      # drafts produced by kol-outreach-product-pitch-email
│   └── negotiation        # drafts produced by kol-outreach-negotiation-email
├── sent/
│   ├── initial
│   ├── product_pitch
│   └── negotiation
├── pending-reply          # ALL inbound KOL replies land here (filter rule)
├── replied/
│   ├── interested
│   ├── asks_materials
│   ├── proposes_rate
│   ├── counter_offers
│   ├── declines
│   ├── ooo
│   └── other
└── closed/                # final state, manual or skill-driven
```

### Required Gmail filter
Create one filter that auto-applies `kol-outreach/pending-reply` to inbound
messages on KOL threads. Easiest implementation:

- Match: `to:<your_outreach_address>` AND `label:kol-outreach/sent/*`
- Action: apply label `kol-outreach/pending-reply`, mark unread, skip Inbox

(Replace `<your_outreach_address>` with the actual outreach mailbox.)

The dispatcher relies on `pending-reply` + unread = "new" and on the
label-move `pending-reply → replied/<intent>` for idempotency.

## 2. Campaign config

For each campaign, copy the example file into place:

```
mkdir -p ~/.hermes/kol-outreach/<campaign_id>
cp hermes-agent/skills/social-media/kol-outreach-orchestrator-flow/config.yaml.example \
   ~/.hermes/kol-outreach/<campaign_id>/config.yaml
# edit values
```

The orchestrator also creates this file if it is missing, by asking the user
to fill the required fields in chat.

## 3. Register the reply dispatcher cron

Run once per machine. The dispatcher is the only cron job in this system.

```
cronjob(
  action="create",
  name="kol-reply-dispatcher",
  schedule="*/10 * * * *",
  skills=["kol-reply-dispatcher"],
  deliver="local",
  reason="Pull KOL replies, classify, draft next step, escalate low-confidence cases."
)
```

To pause / resume / inspect:

```
cronjob(action="list")
cronjob(action="pause",  job_id="...")
cronjob(action="resume", job_id="...")
cronjob(action="trigger", job_id="...")   # one-off run for testing
```

## 4. TEST MODE dry-run checklist

Before flipping a campaign to LIVE:

1. `mode: TEST` and `test_mode_to` set to your own mailbox.
2. Orchestrate a campaign with 3 mock KOLs (use Instagram handles you control,
   put your own email in their Kanban cards as `email`).
3. Verify in Gmail:
   - 3 drafts appear under `kol-outreach/pending/initial`.
   - Each draft's `To:` is the test inbox; first body line reads
     `Intended recipient: <real_email>`.
   - No SKU links in the initial draft.
4. Reply to one of the drafts from the test inbox with text like
   `interested, can you share product info?`. After ≤ 10 minutes, expect a
   product-pitch draft on the same thread, with 2-4 whitelisted SKU URLs.
5. Reply with `our rate is $X`. Expect either an accept/counter draft (if X
   is safe) or an escalate message in chat (if X is in the floor zone).
6. Only after all four checks pass, edit `config.yaml` to `mode: LIVE` and
   inform the agent in chat with the exact words `LIVE MODE`.

## 5. Rollback

```
# Disable the dispatcher
cronjob(action="pause", job_id="<dispatcher_job_id>")

# Drop a campaign
rm -rf ~/.hermes/kol-outreach/<campaign_id>

# Drop the entire skill suite (uninstall)
rm -rf hermes-agent/skills/social-media/kol-outreach-orchestrator-flow \
       hermes-agent/skills/social-media/kol-outreach-initial-email \
       hermes-agent/skills/social-media/kol-outreach-product-pitch-email \
       hermes-agent/skills/social-media/kol-outreach-negotiation-email \
       hermes-agent/skills/social-media/kol-reply-dispatcher
```

No state lives outside the campaign directory and the Gmail labels.
