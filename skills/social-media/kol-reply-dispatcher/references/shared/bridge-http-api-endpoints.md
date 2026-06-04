# Bridge HTTP / CLI endpoints (kol-reply-dispatcher)

Use **`kol_bridge_tool.py`** only — never `curl` ad hoc, never `import dispatch_router`,
never `execute_code` for routing. Base URL: `HERMES_KOL_OPS_BRIDGE_BASE`
(default `http://127.0.0.1:8080/api/plugins/kol-ops-bridge`).

**`--env`:** pass **`LIVE`** for production KOL data or **`TEST`** for sandbox —
not `prod` / `production` (CLI accepts those aliases and normalizes to `LIVE`).
Every read/write that touches CAL must include explicit `--env`; there is no default.

## Reads (GET)

| Purpose | CLI | HTTP |
|---------|-----|------|
| Dispatch bundle | `get-dispatch-context --identity-id ID --campaign-id CID --env LIVE` | `GET /identities/{id}/dispatch-context?campaign_id=&env=` |
| Campaign facts only | `get-facts --identity-id ID --campaign-id CID --env LIVE` | `GET /facts/{id}?campaign_id=&env=` |
| Poller idempotency | *(poller only)* | `GET /identities/{id}/reply-dispatch-status?campaign_id=&message_id=&env=` |
| Follow-up chase hint | `get-reply-chase-hint --identity-id ID --campaign-id CID --message-id MID --thread-id TH --env LIVE` | `GET /identities/{id}/reply-chase-hint?campaign_id=&message_id=&thread_id=&env=` |
| Parsed escalation rules | `get-parsed-escalation-rules` | `GET /policies/escalation_rules/parsed` |

`get-dispatch-context` returns `{goals, lanes, relationship, reusable_facts,
campaign_config, campaign_facts, identity_facts, candidate, learning_hints}`. Use
`campaign_facts` for negotiation state (`offer.barter_attempted`,
`offer.rate_requested`, `offer.proposed_amount`, …). `get-facts` is an
optional narrow read when only campaign facts are needed.

### Learning exports (GET)

| Purpose | CLI | HTTP |
|---------|-----|------|
| Fact corrections | `export-fact-corrections --env LIVE` | `GET /learning/fact-corrections?env=` |
| Negotiation history | `export-negotiation-history --env LIVE` | `GET /learning/negotiation-history?env=` |
| Reject events | `export-reject-events --env LIVE` | `GET /learning/reject-events?env=` |
| Edit events | `export-edit-events --env LIVE` | `GET /learning/edit-events?env=` |
| Job audit trail | `list-learning-job-runs --env LIVE` | `GET /learning/job-runs?env=` |

### Learning apply (POST, bridge key)

| Purpose | CLI | HTTP |
|---------|-----|------|
| Distill reject few-shot | `apply-reject-policy --env LIVE` | `POST /learning/apply-reject-policy` |
| Distill company style | `apply-edit-policy --env LIVE` | `POST /learning/apply-edit-policy` |
| Distill pricing report | `apply-pricing-calibration-policy --env LIVE` | `POST /learning/apply-pricing-calibration-policy` |
| Promote one campaign ratio | `apply-pricing-campaign --env LIVE --campaign-id CID` | `POST /learning/apply-pricing-campaign` |

### Learning cron (POST, bridge key)

| Purpose | CLI | HTTP |
|---------|-----|------|
| Run scheduled suite | `run-learning-jobs --suite nightly` (LIVE only) | `POST /learning/run-scheduled-jobs` body `env: LIVE` |
| Distill only | `run-learning-jobs --suite distill` | same |
| Gmail capture | `run-learning-jobs --suite capture` | same |

Autonomous learning **must** use `env=LIVE`. TEST is rejected at the API.

Policies: `reply_learning`, `pricing_calibration` (auto-versioned by nightly LIVE jobs).

Nightly/audit jobs also include `sync_failure_examples` (append manual corrections to
`kol-email-stage-classifier/references/failure-examples.md`) and
`classifier_eval_deterministic` (golden-set sanitize checks; job `error` on failure).

Optional: set `KOL_LEARNING_USER_STYLE_OWNER_ID` so nightly also runs `apply_edit_user_style`.

See `playground/learning/CRON.md` for recommended crontab.

## Deterministic logic (POST `/logic/*`, no bridge key)

| Step | CLI subcommand | Body keys |
|------|----------------|-----------|
| Compensation offer | `compute-compensation-offer --json '{...}'` | pricing payload (see `kol-pricing-strategist`) |
| Draftable goals | `select-draftable-plan --json '{...}'` | `goals`, `facts`, `signals`, `meta` |
| Escalation rules | `match-escalation-rules --json '{signals,...}'` | `signals`, optional `parsed` |
| Classifier sanitize preview | `sanitize-classifier-facts --json '{namespaces,signals}'` | `namespaces`, `signals` |

**404 on `/logic/select-draftable-plan`** → stop the run, log `bridge_stale_or_down`,
open escalation — do **not** import Python modules or reimplement routing in terminal.

For `select-draftable-plan`, merge facts as:
`{**reusable_facts.facts, **campaign_facts}` from dispatch context.

## Mutations (require `X-Bridge-Key` + `--env`)

| Step | CLI | HTTP |
|------|-----|------|
| Classifier facts | `write-facts-multi --identity-id ID --json '{campaign_id,source,signals,namespaces}'` | `POST /facts/{id}/multi` |
| Fragment merge facts | same; `source=fragment-merge:<message_id>` | same |
| Persist draft | `persist-reply-draft --json '{...}'` | `POST /reply-drafts/persist` |
| Open escalation | `open-escalation --json '{reason,...}'` | `POST /escalations` |
| Reject draft | `reject --fact-path approval.reply_draft --json '{..., correction:{tags,note,suggested_fix}}'` | `POST /approvals/{fact_path}/reject` |
| Unmark (reprocess) | `unmark-reply-handled --message-id MID --identity-id ID --campaign-id CID --detected-mailbox-user-id UID` | `POST /gmail/unmark-reply-handled` |

`write-facts-multi` with `source=email:<message_id>` must include classifier **`signals`**
(same turn) so the Bridge can sanitize premature committed keys.

`persist-reply-draft` with multiple `contributing` entries: set
`child_skill` to `kol-reply-synthesizer` (Bridge defaults this if omitted).

`mark-reply-handled`: pass `--identity-id`, `--campaign-id`, and
`--detected-mailbox-user-id` so labels apply on the operator inbox that
received the reply. `kol-outreach/pending-reply` is optional in Gmail; missing label
does not fail the call (handled label is still applied).
