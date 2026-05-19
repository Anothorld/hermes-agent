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

## Auth

Plugin's HTTP routes go through the dashboard session-token middleware
just like core API routes (see `kanban/dashboard/plugin_api.py` header
for the contract). The external Web backend additionally holds an
API key (stored in `~/.hermes/kol-ops-bridge/secrets.yaml`, 600 perm,
gitignored) that is checked in `_check_external_token` for the subset
of routes intended for the external console rather than the dashboard.

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
