# kol-ops-bridge

API-only plugin that backs the external **KOL Ops Console**. It is the single
source of truth for per-KOL conversation history (audit), and the only path
the external Web system uses to start / read / write KOL outreach state.

## What lives here

- **Conversation Audit Layer (CAL)** — independent SQLite at
  `~/.hermes/kol-ops-bridge/cal.db` with 6 tables:
  - `kol_identity` — global KOL entity (one row per real person, dedup
    across products/campaigns).
  - `kol_conversation_events` — append-only business-semantic event log
    (every stage transition / draft / send / reply / escalation /
    contract / logistics / content event).
  - `kol_draft_history` — every draft + `context_snapshot_json` (why
    was this email generated: selling-point group, prior reply quotes,
    hit SKUs, budget/floor at the time, KOL stage).
  - `kol_reply_history` — every classified reply with `match_strategy`
    + `match_confidence`.
  - `kol_negotiation_history` — full per-round request/counter/decision
    series.
  - `kol_identity_alias` — `(kind, value)` index: thread_id /
    message_id / email / handle → kol_identity_id. Lets the dispatcher
    re-link replies when threadId breaks.
  - `escalation_history` — escalation reasons + classifier confidence
    + human decision.
- **Bridge HTTP API** — mounted at `/api/plugins/kol-ops-bridge/`. Reads
  CAL, writes through skill-facing helpers, and proxies `/start` to the
  Hermes Gateway `POST /v1/runs` to spawn orchestrator runs from Web.
- **Python helpers (`cal.py`)** — skills import these to write CAL.
  Failure is logged but never raises (per design: CAL writes must not
  block skill main flow; reconcile job back-fills from Gmail/Kanban).
- **Safe Bridge CLI (`scripts/kol_bridge_tool.py`)** — deterministic
  agent-facing wrapper for CAL-affecting operations. Dispatcher agents
  must call this CLI or the Bridge HTTP API instead of writing SQL or
  running ad hoc scripts against `~/.hermes/kol-ops-bridge/cal.db`.
  A **compatibility shim** at `plugins/kol-ops-bridge/kol_bridge_tool.py`
  forwards to `scripts/` if something omits the `scripts/` segment.

## CLI pitfalls (agents & operators)

| Mistake | What happens | Fix |
|--------|----------------|-----|
| `python plugins/kol-ops-bridge/kol_bridge_tool.py` (no `scripts/`) | Shim forwards + stderr notice; or use canonical path | Always: `python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py` |
| Running with no subcommand | Exit 2 + **Hint** pointing at `--help` | Add subcommand, e.g. `health`, `get-escalation` |
| `get-escalation --campaign-id …` | Preflight **invalid_cli_args** JSON + hint | Use `--escalation-id` only; filter campaigns via `list-escalations --env LIVE` |
| Empty terminal + exit 2 | Agent only sees **stdout** — stderr-only errors looked like blank output | Read the last stdout line for `{"error":...,"hint":...}`; all failure paths emit there |
| Swallowing stderr (`2>/dev/null`) | Hides human-facing mirror only | Errors are on stdout first; keep stderr visible when debugging on a TTY |
| Expecting direct SQLite | CLI only hits HTTP (`serve.py` must be up) | Start bridge / check `health` first |
| Agent `execute_code` + `curl` + hardcoded `BRIDGE_KEY` | Bypasses CLI; leaks secrets | See **Agent bridge contract** below |

## Agent bridge contract (gateway / kol-orchestrator)

Shared lint + brief text: `bridge_agent_contract.py` (`CLI_INVOCATION` uses
**`python3`**; gateway briefs should use absolute **`kol-bridge-cli`** via
`cli_invocation_abs(repo_root)` — wrapper always resolves `python3` + absolute
`kol_bridge_tool.py`). Hermes hook:
`plugins/kol-bridge-agent-guard/` (blocks curl / source reads on `kol-*` sessions).

**Cold outreach persist:** `persist-initial-outreach-draft` — stable
`draft:outreach_{campaign_id}_{identity_id}` anchors; do not use `write-facts`
on `approval.reply_draft`.

Console resume and draft-preview runs embed hard rules in gateway instructions.
Skills (`kol-escalation-resumer`, `kol-reply-dispatcher`) repeat the same contract.

- **Reads/writes:** `kol_bridge_tool.py` subcommands only (no curl / execute_code HTTP).
- **Email thread:** `get-email-conversation --identity-id … --campaign-id … --env LIVE`
- **Draft persist:** `persist-reply-draft` (not reading `plugin_api.py` in execute_code).
- **Lint:** `kol_bridge_tool.py lint-agent-code --snippet-file … --strict`
- **Doc:** `agent_prj/docs/kol-bridge-agent-tooling.md`

### Escalation pending inbounds (`awaiting_answer`)

When a `kol_inbound_reply` event is written (`POST /events` or poller), Bridge
appends the message to **one** inbound-tagged open escalation on that
identity+campaign (`escalation_inbounds.select_escalation_ids_for_followup`:
same Gmail `thread_id` preferred, else newest inbound-tagged row). Follow-ups
also append a `【KOL 追信 · <msg_id>】` block to `question_to_operator`.

- `open_escalation` seeds `pending_inbounds` from `source_message_id` when present.
- Legacy rows: `POST /escalations/{id}/sync-pending-inbounds` backfills from
  timeline events (Console calls this on inbound-context load).
- While escalation is open, `persist-reply-draft` without `linked_escalation_id`
  returns **409**; dispatcher should see `defer_escalation` in chase hint.

`get-escalation` example (no `--campaign-id`):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-escalation --escalation-id 108
```

## Agent-safe operations

Use the Bridge API, or the CLI wrapper below, for deterministic CRUD-like
operations:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-identity \
  --env TEST \
  --primary-handle "home_style_lover" \
  --platform instagram

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-event \
  --env TEST \
  --identity-id 9 \
  --campaign-id "TS8319 Test" \
  --event-type inbound_reply \
  --actor gmail:reply-poller \
  --json '{"payload":{"gmail_message_id":"...","intent":"brief_budget_question","confidence":0.92}}'

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py write-facts \
  --env TEST \
  --identity-id 9 \
  --json '{"campaign_id":"TS8319 Test","namespace":"offer","source":"skill:negotiation","facts":{"offer.latest_requested_amount":1200,"offer.latest_counter_amount":1000}}'

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-candidate-handles \
  --env TEST \
  --campaign-id "TS8319 Test" \
  --plain

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py mark-reply-handled \
  --env LIVE \
  --message-id "19e749bada32cc15" \
  --identity-id 42 \
  --campaign-id "TS8319" \
  --detected-mailbox-user-id 1

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py approve \
  --env LIVE \
  --fact-path approval.reply_draft \
  --identity-id 42 \
  --campaign-id "TS8319" \
  --decided-by "cli:alice@company.com" \
  --operator-user-id 1

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-escalation \
  --escalation-id 42

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-campaign \
  --env TEST --campaign-id "TS8319 Test" \
  --json '{"paid_target_budget": 500, "paid_ceiling": 1500}'
```

Partial-field `upsert-campaign --json` merges into the existing
`campaign_config` row (only supplied columns are updated). Use canonical
column names (`paid_target_budget`, `paid_ceiling`, `sku_whitelist`, …);
unknown keys are ignored. For KOL compensation, `paid_target_budget` and
`paid_ceiling` both refer to the extra cash supplement on top of the gifted
product, not the product value itself.

**`deliverable_count_per_platform` (frequent agent mistake):** must be a
**single positive integer** in `upsert-campaign` JSON — the same count applies
to every platform listed in `deliverable_platforms`. Example:
`"deliverable_platforms": ["instagram","tiktok"], "deliverable_count_per_platform": 1`.
Do **not** send a per-platform map like `{"instagram": 1, "tiktok": 1}`; that
shape is for `offer.*` negotiation facts (`offer.deliverable_count_proposed`,
`offer.deliverable_count_per_platform`), not for `campaign_config`. If every
platform in a map shares the same count, the API coerces it to that int; mixed
counts are rejected. See `docs/kol-campaign-config-upsert.md`.

The wrapper requires explicit `env` for mutating calls and never imports or
opens CAL SQLite directly.

**`--env` (frequent agent mistake):** only **`TEST`** or **`LIVE`** are stored in
CAL. Production / real KOL data = **`LIVE`**, not `prod`, `production`, or
`live` (lowercase is accepted and normalized). Sandbox / test inbox flows =
**`TEST`**. The CLI maps `prod` / `production` → `LIVE` and `dev` → `TEST`
with a stderr notice.

Use dedicated projection commands such as
`list-candidate-handles` instead of piping `list-candidates` into ad hoc
`python -c` snippets.

### Outreach touch cooldown (14 days)

Cross-campaign **confirmed outreach sends** (`outreach.sent` events and
`offer.outreach_sent_at` facts) drive two behaviors:

1. **Discovery block (14-day cooldown)** — `add-candidate` returns HTTP 409
   `outreach_cooldown_active` when the identity was outreached in the last
   14 days. Skills should pre-filter with:

   ```bash
   python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-outreach-cooldown-handles \
     --env LIVE --plain
   ```

2. **Discovery block (prior collab)** — `add-candidate` and
   `ingest-confirmed-candidate` return HTTP 409 `discovery_skip_active` when
   the identity has `last_outcome` in
   `competitor | success | aborted | legacy_collab`. Skills should pre-filter with:

   ```bash
   python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py list-discovery-skip-handles \
     --env LIVE
   ```

   Response is JSON: `items[]` with `{handle, reason}` per row (omit `--plain` so
   `reason` is available for operator logs).

3. **Console tags** — `GET /identities/outreach-touch?identity_ids=1,2,3`
   enriches shortlist rows and KOL detail with `prior_outreach_touch`
   (`last_touch_at`, `within_cooldown`, optional `last_touch_campaign_id`).

### Shortlist campaign transfer (Phase 1a)

Move a KOL between campaign discovery pools **before** shortlist approval (no
Gmail thread, archive, or gateway run):

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py transfer-campaign \
  --identity-id 42 --from-campaign-id CAMPAIGN-A --to-campaign-id CAMPAIGN-B \
  --env LIVE --reason "better product fit"
```

HTTP: `POST /identities/{id}/transfer-campaign` with
`from_campaign_id`, `to_campaign_id`, `env`, `reason`. Source row →
`rejected`; target row → `discovered` (`source=operator_transfer`).

### KOL registry (metrics table)

`GET /kol-registry?env=LIVE&limit=50&offset=0` returns every identity in
`campaign_candidates` (Agent discovery only; legacy red-list imports are
excluded). Each row includes `internal_touch_count` (row matches across
**all sheets** in ``曾触达列表.xlsx`` — +1 per matching spreadsheet row;
fallback ``data/prior_touch_allowlist.json``),
`followers` /
`target_spu` / `ig_url`, and batched Nox/identity facts for the console
**红人列表** on `/metrics`.

By default the bridge reads ``~/Documents/曾触达列表.xlsx`` when present
(new spreadsheet rows apply on the next registry request; mtime-cached).
Bundled ``data/prior_touch_allowlist.json`` is the fallback on servers
without that path. Override with ``KOL_PRIOR_TOUCH_ALLOWLIST_XLSX`` /
``KOL_PRIOR_TOUCH_ALLOWLIST_JSON``. Refresh the JSON bundle after updates:

```bash
python plugins/kol-ops-bridge/scripts/import_prior_touch_allowlist.py \
  ~/Documents/曾触达列表.xlsx
```

### Confirmed-candidate ingest guardrails

For discovery persistence, prefer one-call deterministic ingest:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id "<campaign_id>" --env LIVE --json @/tmp/ingest_<handle>.json
```

Key payload rules (most frequent failure modes):

- JSON must be **nested** `IngestConfirmedCandidateBody`: top-level `source`,
  `identity` (`primary_handle`, not `handle`), and `candidate` (`source` required).
  Flat `handle` / `profile_url` / `bio` objects fail with `json_missing_field`.
- Treat top-level `source` and `identity.*_source` as different fields.
  - top-level `source`: workflow origin (for example `skill:instagram-kol-discovery`)
  - `identity.*_source`: strict enum only (`google_search_result`, `linktree`,
    `ig_bio`, `facebook_about`, `fb_creator_profile`, `personal_site`,
    `media_kit`, `agency_page`, `ig_profile_and_reels`, `ig_reel_pick`,
    `llm_summary`)
- Every `identity.*_url` must be an absolute `http(s)` URL.
- `identity.linktree_url` host allowlist: `linktr.ee`, `beacons.ai`,
  `bio.link`, `lnk.bio`, `solo.to`, `linkin.bio`.
- Optional-field policy: if an optional field fails validation, remove/fix
  that field and retry the same handle; do not guess alternate formats
  repeatedly.

Canonical shape examples and skill-side persistence conventions:
`skills/social-media/instagram-kol-discovery/references/bridge-cli-json-payloads.md`.

### NoxInfluencer integration

See `docs/kol-nox-integration.md` and plugin `plugins/nox-kol-bridge/`.
Allowed identity facts include `identity.nox_creator_id`,
`identity.nox_diligence_verdict`, `identity.nox_diligence_at`, and monitor IDs.
`identity.email_source` may be `noxinfluencer_api` when contacts came from Nox.

### Veedcrawl persist (discovery supplement)

See `docs/kol-veedcrawl-integration.md` and plugin `plugins/veedcrawl/`.
Monthly cache + blobs live at `$HERMES_HOME/kol-ops-bridge/veedcrawl_cache/`.
The veedcrawl plugin calls `veedcrawl_persist.fetch_with_persist()` so one tool
invocation atomically hits cache, calls REST, and stores the full JSON response.

Allowed identity index facts include `identity.veedcrawl_profile_followers`,
`identity.veedcrawl_recent_reels_stats`, `identity.veedcrawl_cache_month`,
`identity.veedcrawl_cache_key`, `identity.veedcrawl_storage_ref` (alias
`identity.veedcrawl_blob_ref`), and `identity.veedcrawl_extract_summary`.
Metadata cache keys are SHA-256 hashed in CAL to avoid URL length limits.
Blobs are the source of truth; facts are summaries for skills and the console.

Ops CLI (non-agent path):

```bash
python plugins/kol-ops-bridge/scripts/veedcrawl_cache_tool.py cache-stats
python plugins/kol-ops-bridge/scripts/veedcrawl_cache_tool.py cache-lookup --cache-key 'profile:ig:handle:limit=12'
```

## Toolized deterministic skill steps

Several KOL skill steps used to be model-generated reasoning. They are now
**pure, server-side decision functions** exposed as CLI subcommands + HTTP
endpoints so the number / verdict / routing is reproducible and shared by the
Web console. The agent calls the tool instead of re-deriving the logic.

| Concern | Pure module | CLI subcommand | Endpoint |
|---|---|---|---|
| Compensation number / bounds / human-gate | `pricing_engine.py` | `compute-compensation-offer` | `POST /logic/compute-compensation-offer` |
| Campaign-config safety-field validation | `campaign_validation.py` | `validate-campaign-config` | `POST /logic/validate-campaign-config` |
| Per-turn lane routing (primary skill + side-topics) | `dispatch_router.py` | `select-next-skill` | `POST /logic/select-next-skill` |
| Multi-goal draftable plan (fragment dispatch) | `dispatch_router.py` | `select-draftable-plan` | `POST /logic/select-draftable-plan` |
| Escalation-rule matching → `escalation_hint` | `policies.match_escalation_rules` | `match-escalation-rules` | `POST /logic/match-escalation-rules` |
| Classifier committed-key sanitize (preview) | `classifier_facts.py` | `sanitize-classifier-facts` | `POST /logic/sanitize-classifier-facts` |
| Reply poller idempotency probe | `cal.reply_dispatch_status` | *(GET only)* | `GET /identities/{id}/reply-dispatch-status` |
| Follow-up chase policy | `reply_chase.py`, `cal.reply_chase_hint` | `get-reply-chase-hint` | `GET /identities/{id}/reply-chase-hint` |
| Gmail unmark for re-dispatch | `gmail_client.py` | `unmark-reply-handled` | `POST /gmail/unmark-reply-handled` |
| Reply-draft envelope enrichment + atomic persist | `reply_draft.py` | `persist-reply-draft` | `POST /reply-drafts/persist` |
| Learning exports (read-only) | `learning_store.py` | `export-*-events`, `export-fact-corrections`, … | `GET /learning/*` |
| Learning apply (distill) | `learning_distill.py` | `apply-*-policy`, `apply-pricing-campaign` | `POST /learning/apply-*` |
| Learning cron (autonomous) | `learning_jobs.py` | `run-learning-jobs`, `list-learning-job-runs` | `POST /learning/run-scheduled-jobs`, `GET /learning/job-runs` |
| Learning LLM distill | `learning_llm.py` | *(via apply-edit-policy)* | Reuses Hermes `model.default` + `~/.hermes/.env` via `call_llm`; override with `KOL_LEARNING_LLM_*` |
| Reject tag vocabulary | `reject_tags.py` | *(via reject body)* | `POST /approvals/.../reject` + `correction` |
| Sent-body edit diff | `gmail_reconcile.py`, `reply_diff.py` | `reconcile_sent`, `backfill_edit_learning` | `POST /gmail/reconcile-sent`, `POST /learning/backfill-edit-learning` |
| Gmail coordinator | `gmail_worker.py` | — | `GET /gmail/worker/status` |
| Inbound reply polling | `gmail_inbound_poller.py`, `gmail_inbound_dispatch.py`, `scripts/kol_reply_dispatcher.py` | `kol_reply_dispatcher --watch` | `GET/POST /gmail/inbound-poller/*` |

`write-facts-multi` with `source=email:<message_id>` auto-sanitizes namespaces
when `signals` are supplied (see `classifier_facts.sanitize_classifier_namespaces`).

The `/logic/*` endpoints above (except reply-draft persist) are pure (no DB
read/write) and need no bridge
key. `persist-reply-draft` writes CAL (event + `approval.reply_draft` fact in
one call, after enriching `to` / `Re:`-subject / `thread_id`) and requires the
key like every other mutating route. Child `body` must be **new prose only** —
``On … wrote:`` / ``>`` quote blocks are stripped at persist time (bridge re-adds
one Gmail quote on approve).

**Thread anchors on `approval.reply_draft`:** every write must carry at least
one of `draft.thread_id`, `source_message_id`, top-level `thread_id`, or
`in_reply_to` (CAL rejects anchor-less drafts at write time). Direct
`write-facts` from `skill:kol-*` on `approval.reply_draft` is rejected — use
`persist-reply-draft` only.

**Follow-up chase (`reply_chase`):** when a new inbound arrives while an older
pending (or approved-but-unsent) `approval.reply_draft` exists, the poller
attaches `chase_context.recommended_action=regenerate` to `pending_replies[]`.
The dispatcher must supersede via `persist-reply-draft` (writes
`kol_reply_draft_superseded` + `chase_supersede` on the new fact). Writing
`approval.pending_action_reply_needed` alone is blocked when chase says
regenerate. On approve, the bridge resolves Gmail `threadId` from thread anchor
fields — including legacy synthesizer top-level `thread_id` / `in_reply_to`.

**Orphan Gmail draft discard:** when chase supersedes an
**approved-but-unsent** prior draft, `persist-reply-draft` best-effort
deletes the old Gmail `draftId` (via `gmail delete-draft`) and clears stale
`offer.gmail_draft_id` / `offer.gmail_thread_id`. Outcome is recorded on
`chase_supersede.orphan_gmail_discard` and in `kol_reply_draft_superseded`.
Failures are logged but do not block supersede.

**Gmail draft on approve:** `POST /approvals/.../approve` for `approval.reply_draft`
creates an HTML draft in the correct thread (Reply-all Cc + `In-Reply-To`).
Before calling Gmail, the bridge verifies `thread_id` against the operator mailbox
(`gmail_thread_resolve.py`): synthetic tokens (e.g. `proactive-followup:…`) and
message ids mistaken for thread ids are rejected or corrected so
`drafts.create` does not return `400 Invalid thread id value`.
**Initial cold/re-engagement outreach** (`kol-cold-outreach`, anchors
`draft:outreach_{campaign}_{identity}` / `outreach_{campaign}_{identity}`) skips
thread attach — Gmail creates a new standalone draft with no `threadId`.
**Proactive operator follow-up** (`kol-proactive-followup`, `primary_goal=proactive_followup`)
always attaches to the existing thread: `persist-reply-draft` replaces synthetic
thread anchors with `offer.gmail_sent_thread_id` / timeline ids; approve sets
`threadId`, `In-Reply-To` (thread tail when source is synthetic), and Gmail quote.
Quoted history mirrors **Gmail web Reply**: parent MIME HTML (when available) is
embedded inside ``gmail_extra`` + ``gmail_quote`` + ``blockquote type="cite"`` —
the same wrapper Gmail-sent replies use for the collapsible ``…`` control.
``In-Reply-To`` / ``References`` target the inbound Gmail message id via
``--reply-to-message-id`` on draft create.

```bash
# Pricing: returns {mode_decided, target_number, lower/upper_bound,
#   requires_human_gate, gate_reason, suggested_wording, rationale_one_line}
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
  compute-compensation-offer --json @/tmp/pricing.json

# Lane routing: returns {primary_lane, primary_goal, primary_skill,
#   side_topics, severity_reversal_applied}
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
  select-next-skill --json @/tmp/dispatch.json

# Persist a reply draft (replaces the dispatcher's two hand-built writes)
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
  persist-reply-draft --env TEST --json @/tmp/draft.json
```

## Auth

Plugin's HTTP routes go through the dashboard session-token middleware
just like core API routes (see `kanban/dashboard/plugin_api.py` header
for the contract). The external Web backend additionally holds an
API key (stored in `~/.hermes/kol-ops-bridge/secrets.yaml`, 600 perm,
gitignored) that is checked in `_check_external_token` for the subset
of routes intended for the external console rather than the dashboard.
The agent-facing CLI resolves the same key from `--bridge-key`,
`HERMES_KOL_OPS_BRIDGE_KEY`, console compatibility aliases, or that
`secrets.yaml` file. In source-tree dev mode it also falls back to
`playground/kol-ops-console/.env`, so gateway-spawned agents do not depend
on inheriting the console backend's environment.

## Stuck-goal scan (DingTalk follow-up)

`POST /admin/check-stuck-goals` scans `kol_goal_state` for goals whose
`updated_at` exceeds the campaign's `followup_intervals[goal]` (default
72h) and emits a DingTalk card per stuck row via the bridge's
notifier. The endpoint is idempotent — it just reads + notifies.

Wire it to a system cron (or any external scheduler) so the operator
gets pinged when a deal hasn't moved in a long time. Sample crontab:

```cron
# Every hour at :17, scan stuck goals in both envs (TEST first so
# fixtures don't drown out real LIVE pings).
17 * * * * cd /path/to/hermes-agent && \
  ./.venv/bin/python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
    check-stuck-goals --env TEST >/dev/null 2>&1
23 * * * * cd /path/to/hermes-agent && \
  ./.venv/bin/python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
    check-stuck-goals --env LIVE >/dev/null 2>&1
```

Override the default 72h threshold per campaign via `campaign_config.
followup_intervals` (e.g. `{"compensation_negotiation": 48}`). Plan C6
recommends 48h for most flow goals — set it during campaign creation
or via `PUT /campaigns/{id}`. Notification env vars: see
`notifier.py` (`HERMES_DINGTALK_WEBHOOK`, `HERMES_DINGTALK_SECRET`,
`HERMES_KOL_CONSOLE_BASE_URL`).

## Learning cron (autonomous)

Tier-1 learning (reject/edit distill with **style proposals → Console approval**,
pricing calibration, Gmail sent capture)
runs **on LIVE only** without manual playground scripts. Schedule via system
cron — see `playground/learning/CRON.md`.

```cron
# Every 15m — capture operator Gmail edits into draft_edit_learning
*/15 * * * * cd /path/to/hermes-agent && \
  ./.venv/bin/python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
    run-learning-jobs --suite capture --triggered-by cron:learning:capture

# Daily 03:20 — distill policies + LIVE pricing promote + fact-correction audit
20 3 * * * cd /path/to/hermes-agent && \
  ./.venv/bin/python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
    run-learning-jobs --suite nightly --triggered-by cron:learning:nightly
```

`run-learning-jobs` rejects `--env TEST`. Audit:
`kol_bridge_tool list-learning-job-runs --env LIVE` or `GET /learning/job-runs`.
Disable all jobs: `KOL_LEARNING_JOBS_DISABLED=1`.

## TEST/LIVE isolation

Every row carries an `env` column (`TEST` | `LIVE`). The reconcile / clean
jobs honour this so test data can be wiped without touching production
rows.

## Failure policy

CAL writes are best-effort: skill callers wrap every write in a try /
except that logs and returns. The reconcile loop (`cal.reconcile_*`)
periodically walks Gmail labels + Kanban cards to back-fill anything
that was dropped during a write failure.

## Per-operator Gmail (multi-mailbox)

See `docs/kol-operator-gmail-onboarding.md` for operator SOP. Bridge/poller env
template: `plugins/kol-ops-bridge/.env.example`. Post-upgrade one-shot:

```bash
python hermes-agent/playground/kol-ops-console/scripts/gmail_multimailbox_setup.py
```

CLI approve for `approval.reply_draft` requires mailbox context:
`--operator-user-id`, `--operator-email`, `decided-by web:you@co`, or
`KOC_DEFAULT_OPERATOR_USER_ID`.

## Not in scope (yet)

- The external Web backend (FastAPI) and SPA frontend — those live in
  `playground/kol-ops-console/`.
- The contract / logistics provider adapters — first version is stub-only;
  schema is reserved.
