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
  --message-id "19e749bada32cc15"

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py get-escalation \
  --escalation-id 42

python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py upsert-campaign \
  --env TEST --campaign-id "TS8319 Test" \
  --json '{"paid_ceiling": 1500}'
```

Partial-field `upsert-campaign --json` merges into the existing
`campaign_config` row (only supplied columns are updated). Use canonical
column names (`paid_ceiling`, `sku_whitelist`, …); unknown keys are
ignored.

The wrapper requires explicit `env` for mutating calls and never imports or
opens CAL SQLite directly. Use dedicated projection commands such as
`list-candidate-handles` instead of piping `list-candidates` into ad hoc
`python -c` snippets.

### Confirmed-candidate ingest guardrails

For discovery persistence, prefer one-call deterministic ingest:

```bash
python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py ingest-confirmed-candidate \
  --campaign-id "<campaign_id>" --env LIVE --json @/tmp/ingest_<handle>.json
```

Key payload rules (most frequent failure modes):

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
| Reply-draft envelope enrichment + atomic persist | `reply_draft.py` | `persist-reply-draft` | `POST /reply-drafts/persist` |

`write-facts-multi` with `source=email:<message_id>` auto-sanitizes namespaces
when `signals` are supplied (see `classifier_facts.sanitize_classifier_namespaces`).

The `/logic/*` endpoints above (except reply-draft persist) are pure (no DB
read/write) and need no bridge
key. `persist-reply-draft` writes CAL (event + `approval.reply_draft` fact in
one call, after enriching `to` / `Re:`-subject / `thread_id`) and requires the
key like every other mutating route.

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

## TEST/LIVE isolation

Every row carries an `env` column (`TEST` | `LIVE`). The reconcile / clean
jobs honour this so test data can be wiped without touching production
rows.

## Failure policy

CAL writes are best-effort: skill callers wrap every write in a try /
except that logs and returns. The reconcile loop (`cal.reconcile_*`)
periodically walks Gmail labels + Kanban cards to back-fill anything
that was dropped during a write failure.

## Not in scope (yet)

- The external Web backend (FastAPI) and SPA frontend — those live in
  `playground/kol-ops-console/`.
- The contract / logistics provider adapters — first version is stub-only;
  schema is reserved.
