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
```

The wrapper requires explicit `env` for mutating calls and never imports or
opens CAL SQLite directly.

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
17 * * * * cd /home/pc/agent_prj/hermes-agent && \
  ./.venv/bin/python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py \
    check-stuck-goals --env TEST >/dev/null 2>&1
23 * * * * cd /home/pc/agent_prj/hermes-agent && \
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
