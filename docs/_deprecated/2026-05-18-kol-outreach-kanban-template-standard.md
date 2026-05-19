> ⚠️ **DEPRECATED — v1.1 Kanban template standard. DO NOT FOLLOW.**
> Superseded by `hermes-agent/skills/social-media/kol-outreach-orchestrator-flow/SKILL.md` + `SETUP.md`. The template card titles in this file (`shortlist creator discovery`, `humanize ... draft`, etc.) must not be recreated. Kept for history.

# KOL outreach Kanban standard template

Goal: define a clean, reusable creator-outreach Kanban template so the board stays orderly and real execution pipelines can be created from a documented standard instead of reusing old template cards.

Architecture: keep the board for live work only. Keep the canonical process in this document. When starting a new campaign or creator, instantiate new tasks from this template with real campaign and creator values. Do not reuse old sample/template task ids.

Tech stack: Hermes Kanban, `kol-scout`, `outreach-operator`, `instagram-kol-discovery`, `google-workspace`, `humanizer`.

---

## Operating rule

The board should contain only:
- live campaign tasks
- live creator pipeline tasks
- archived historical tasks

The board should not be used as the canonical storage for template definitions.

This document is the canonical template.

---

## Roles

### `kol-scout`
Use for:
- creator discovery
- creator qualification
- contact-email discovery

### `outreach-operator`
Use for:
- Gmail thread reading
- email drafting
- humanizer pass on email drafts
- negotiation summary drafting
- delivery collection summary

### Human reviewer
Use for:
- creator approval
- email/contact approval
- approval of every outbound message
- commercial negotiation decisions
- final asset acceptance

---

## Task naming convention

Use this exact naming pattern for live tasks:

`[campaign:<campaign_slug>] [creator:@handle] <stage>`

Examples:
- `[campaign:summer-sofa-pilot] [creator:@janedoehome] research creator candidate`
- `[campaign:summer-sofa-pilot] [creator:@janedoehome] draft initial outreach email`
- `[campaign:summer-sofa-pilot] [creator:@janedoehome] humanize negotiation draft`

If the creator handle is unknown at campaign-discovery stage, use:
- `[campaign:<campaign_slug>] shortlist creator discovery`

---

## Campaign-level structure

### Parent task: campaign root
Title:
`[campaign:<campaign_slug>] campaign root`

Assignee:
- optional; usually unassigned or assigned to `kol-scout`

Body template:
- Campaign name
- Product / collection
- Market
- Buyer persona
- Creator criteria
- Budget notes
- Mandatory rule: every external send requires explicit human approval
- Notes on whether the campaign is discovery-first or creator-list-first

Purpose:
- top-level container and reference point only

---

## Creator pipeline standard

Create one creator pipeline per creator.

### 1. Research creator candidate
Assignee:
- `kol-scout`

Title:
`[campaign:<campaign_slug>] [creator:@handle] research creator candidate`

Required output:
- creator handle
- profile URL
- follower count
- recent average views
- engagement estimate
- product fit reason
- audience fit reason
- competitor conflict check
- evidence URLs

Rules:
- do not contact creator
- do not infer unavailable facts without labeling them as assumptions

### 2. Review creator candidate
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review creator candidate`

Checklist:
- on brief
- no competitor conflict
- worth contacting
- approve / reject / request more research

### 3. Find creator contact email
Assignee:
- `kol-scout`

Title:
`[campaign:<campaign_slug>] [creator:@handle] find creator contact email`

Required output:
- best email
- source URL
- confidence level
- alternate contacts
- note whether business / management / personal

Rules:
- do not send

### 4. Review contact email
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review contact email`

Checklist:
- trustworthy
- correct route
- approved to proceed to outreach drafting

### 5. Draft initial outreach email
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] draft initial outreach email`

Required output:
- to
- subject
- body
- personalization basis
- CTA
- risks / placeholders

Rules:
- do not send

### 6. Humanize initial outreach draft
Assignee:
- `outreach-operator`
Skill:
- `humanizer`

Title:
`[campaign:<campaign_slug>] [creator:@handle] humanize initial outreach draft`

Required output:
- revised subject
- revised body
- note on what changed

Rules:
- preserve commercial meaning
- do not invent new promises
- do not send

### 7. Review initial outreach email
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review initial outreach email`

Checklist:
- natural tone
- brand intro accurate
- no wrong promises
- safe to send

### 8. Send initial outreach email
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] send initial outreach email`

Required output:
- sent status
- Gmail message id
- Gmail thread id

Rules:
- explicit human approval required in current step
- use Google Workspace only
- never improvise content

### 9. Check reply status
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] check reply status`

Required output:
- thread id
- latest message id
- latest sender
- reply category
- concise summary
- recommended next action

Reply categories:
- no reply
- decline
- interested
- asks for product info
- asks for budget / price
- negotiation
- confirms deal
- sends final delivery
- ambiguous

### 10. Review next-step strategy
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review next-step strategy`

Choices:
- close out
- follow up
- send product links/images
- start negotiation
- ask clarification

### 11. Draft product-share email
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] draft product-share email`

Required output:
- to
- subject
- body
- assets/links referenced
- CTA
- risk notes

Rules:
- do not send

### 12. Humanize product-share draft
Assignee:
- `outreach-operator`
Skill:
- `humanizer`

Title:
`[campaign:<campaign_slug>] [creator:@handle] humanize product-share draft`

Required output:
- revised subject
- revised body
- note on what changed

Rules:
- preserve links/assets
- do not add new commitments
- do not send

### 13. Review product-share email
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review product-share email`

Checklist:
- links/images correct
- message is clear
- safe to send

### 14. Send product-share email
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] send product-share email`

Required output:
- sent status
- message id
- thread id

Rules:
- explicit human approval required in current step

### 15. Summarize negotiation reply
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] summarize negotiation reply`

Required output:
- quoted price
- deliverables
- usage rights
- timeline
- open questions
- recommendation options

Rules:
- do not send
- separate facts from recommendations

### 16. Review negotiation strategy
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review negotiation strategy`

Checklist:
- acceptable price range
- acceptable deliverables
- rights acceptable
- timeline acceptable
- whether to counter / accept / clarify

### 17. Draft negotiation response
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] draft negotiation response`

Required output:
- subject
- body
- commercial objective
- unresolved issues

Rules:
- do not send

### 18. Humanize negotiation draft
Assignee:
- `outreach-operator`
Skill:
- `humanizer`

Title:
`[campaign:<campaign_slug>] [creator:@handle] humanize negotiation draft`

Required output:
- revised subject
- revised body
- note on what changed

Rules:
- do not change numbers, rights, timing, or commercial posture without explicit instruction
- do not send

### 19. Review negotiation response
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review negotiation response`

Checklist:
- commercial position is correct
- tone is appropriate
- safe to send

### 20. Send negotiation response
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] send negotiation response`

Required output:
- sent status
- message id
- thread id

Rules:
- explicit human approval required in current step

### 21. Collect final delivery
Assignee:
- `outreach-operator`

Title:
`[campaign:<campaign_slug>] [creator:@handle] collect final delivery`

Required output:
- received asset links/files
- claimed deliverables
- delivery time
- open issues

### 22. Review final delivery
Assignee:
- human

Title:
`[campaign:<campaign_slug>] [creator:@handle] review final delivery`

Checklist:
- assets received
- deliverables match expectation
- acceptable for closeout or revision

---

## Dependency order

Use this order when instantiating a real creator pipeline:

1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10

If next step is product-share:
10 -> 11 -> 12 -> 13 -> 14 -> 9

If next step is negotiation:
10 -> 15 -> 16 -> 17 -> 18 -> 19 -> 20 -> 9

If final delivery is received:
9 -> 21 -> 22

Important:
- do not pre-create many negotiation rounds
- create one new negotiation loop per actual new reply

---

## Board hygiene rules

1. Do not reuse sample/template task ids for live work.
2. Create fresh live tasks from this document for every campaign and creator.
3. Archive experimental or polluted sample tasks once no longer needed.
4. Keep one creator = one pipeline.
5. Keep every send behind a human review gate.
6. Keep humanizer between draft and human review, never after approval.

---

## Suggested next operating mode

From now on:
- use this document as the source of truth
- create a fresh campaign root when you start real work
- instantiate a creator pipeline only when you have a real creator to process
- avoid maintaining template cards as live board objects

---

## Existing board cleanup recommendation

Current board has old sample/template tasks that were touched by the dispatcher and now have mixed statuses. To avoid confusion:
- keep this document as the template source of truth
- archive old template/sample board tasks after confirming they are no longer needed
- only create fresh real tasks going forward
