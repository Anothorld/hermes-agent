> ⚠️ **DEPRECATED — v1.1 rollout plan. DO NOT FOLLOW.**
> Superseded by `hermes-agent/skills/social-media/kol-outreach-orchestrator-flow/SKILL.md` + `SETUP.md`. The per-task review-gate model and `kol-scout` worker assignment described below are abandoned. Kept for history.

# KOL Outreach Kanban Rollout Plan

Goal: Stand up a human-reviewed KOL outreach workflow in Hermes using Kanban, existing `kol-scout` research capability, a dedicated `outreach-operator` profile, and Google Workspace for Gmail operations.

Current verified environment
- Existing profiles: `default`, `kol-scout`
- Created during this session: `outreach-operator`
- Kanban board exists: `default`
- Google Workspace auth for current profile is NOT configured yet (`NOT_AUTHENTICATED`)

Recommended roles
1. `kol-scout`
   - Discover creators with `instagram-kol-discovery`
   - Search contact emails on the web
   - Summarize creator fit and evidence
2. `outreach-operator`
   - Draft outreach emails
   - Read Gmail threads
   - Summarize reply intent and negotiation state
   - Draft follow-up / pricing / product-share emails
3. Human reviewer
   - Approve every stage before the next stage becomes actionable
   - Approve every external send action

Mandatory skills by role
- `kol-scout`
  - `instagram-kol-discovery`
  - `google-workspace` (for thread lookup context; required once Gmail is used in-flow)
  - `kanban-orchestrator` only if this profile will also create or route cards
- `outreach-operator`
  - `google-workspace`
  - `humanizer` (strongly recommended)
- Optional later:
  - a dedicated `campaign-orchestrator` profile with `kanban-orchestrator`

Human-review-first workflow design

Per creator, use one pipeline with explicit review gates:

1. `research creator candidate`
   assignee: `kol-scout`
   output:
   - profile URL
   - followers / avg views / ER
   - product fit reason
   - evidence links

2. `review creator candidate`
   assignee: human
   checklist:
   - worth contacting
   - no competitor conflict
   - on-brief
   next if approved: email discovery

3. `find creator contact email`
   assignee: `kol-scout`
   output:
   - email
   - source URL
   - confidence
   - alternates

4. `review contact email`
   assignee: human
   checklist:
   - email trustworthy
   - correct contact route
   next if approved: initial email drafting

5. `draft initial outreach email`
   assignee: `outreach-operator`
   output:
   - to
   - subject
   - body
   - personalization basis
   - CTA
   - risk notes

6. `review initial outreach email`
   assignee: human
   checklist:
   - tone okay
   - no wrong promises
   - brand intro accurate
   - ready to send

7. `send initial outreach email`
   assignee: `outreach-operator`
   rule:
   - only created or unblocked after human approval
   - must send via Google Workspace Gmail only after explicit human confirmation

8. `check reply status`
   assignee: `outreach-operator`
   output:
   - no reply / reject / interested / asks price / asks materials
   - Gmail thread id
   - summary

9. `review next-step strategy`
   assignee: human
   decides one of:
   - close out
   - follow up
   - send product links/images
   - enter negotiation

10. `draft product-share email`
    assignee: `outreach-operator`
    output:
    - selected links/images
    - explanation copy
    - CTA

11. `review product-share email`
    assignee: human

12. `send product-share email`
    assignee: `outreach-operator`

13. `summarize negotiation reply`
    assignee: `outreach-operator`
    output:
    - quoted price
    - deliverables
    - rights terms
    - timing
    - open questions

14. `review negotiation strategy`
    assignee: human

15. `draft negotiation response`
    assignee: `outreach-operator`

16. `review negotiation response`
    assignee: human

17. `send negotiation response`
    assignee: `outreach-operator`

18. Repeat 13-17 until resolved.

19. `collect final delivery`
    assignee: `outreach-operator`
    output:
    - file/link received
    - claimed deliverables
    - thread summary

20. `review final delivery`
    assignee: human

Board modeling recommendation
- One campaign parent card for the product/campaign
- One child pipeline parent card per creator
- Then child cards for each step above under that creator pipeline
- Do NOT pre-create many future negotiation rounds; create those when a new reply arrives

Suggested task title convention
- `[creator:@username] research creator candidate`
- `[creator:@username] review creator candidate`
- `[creator:@username] find creator contact email`
- `[creator:@username] draft initial outreach email`
- `[creator:@username] review initial outreach email`
- `[creator:@username] send initial outreach email`
- `[creator:@username] summarize negotiation reply`
- `[creator:@username] review negotiation strategy`

Suggested task body template
- Campaign:
- Creator handle:
- Creator profile URL:
- Current stage:
- Required output schema:
- Human review checklist:
- Do not send any email unless explicitly instructed after approval.

Immediate next actions required from user
1. Finish Google Workspace OAuth for the profile(s) that will read/send Gmail
2. Confirm whether you want me to also configure `outreach-operator` with a role-specific SOUL/instructions
3. Confirm whether you want one shared Gmail operator profile (`outreach-operator`) or keep everything in `kol-scout` initially
4. After Google auth is ready, create the first real campaign board cards

Concrete recommended next build sequence
1. Configure Google Workspace auth
2. Add/confirm needed skills on `outreach-operator`
3. Customize `outreach-operator` role prompt for drafting + Gmail handling + never-send-without-human-approval
4. Create first campaign parent card in Kanban
5. Create creator pipeline template cards for one pilot creator
6. Validate end-to-end on a single creator before scaling
