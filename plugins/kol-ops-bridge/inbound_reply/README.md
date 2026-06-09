# inbound_reply

Gmail inbound polling for KOL outreach: identity match, anomaly gating,
`kol_inbound_reply` event write, and gateway dispatch to `kol-reply-dispatcher`.

## Runtime paths

| Path | Bridge adapter | When |
|------|----------------|------|
| **Worker** (production) | `inbound_reply_ports.in_process.InProcessBridgeAdapter` | `serve.py` → `gmail_worker` → `gmail_inbound_poller` |
| **CLI** (debug) | `inbound_reply_ports.http.HttpBridgeAdapter` | `scripts/kol_reply_dispatcher.py --env TEST` (requires running bridge) |

Both call `inbound_reply.orchestrator.run_once()` with different `InboundDeps`.

## Module layout

- `matcher.py` — strict / weak / detached identity match + anomaly signals
- `gating.py` — content risk and soft-control flags
- `payload.py` — `pending_reply_payload` (thread history, chase context)
- `processor.py` — per-message pipeline (dedup, event write, gateway)
- `state.py` — flock lock, local seen set, Console global dedup
- `orchestrator.py` — one tick over all mailboxes

## Failure / retry semantics

| Outcome | Mark seen? | Next tick |
|---------|------------|-----------|
| `dispatched` | yes | skip (draft/idempotency handles rest) |
| `skipped` | yes | skip |
| `retry` | no | re-process; `should_retry_gateway_only` skips duplicate event write |
| orchestrator crash mid-message | prior successes saved incrementally | failed message retried |

Gateway failures return `retry` (not `dispatched`) so `should_retry_gateway_only`
can recover without duplicate `kol_inbound_reply` rows.

Bridge/matcher infra errors return `retry`. True non-matches return `skipped`.

## Env vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `HERMES_HOME` | `~/.hermes` | `poller_state.json` / flock lock path |
| `KOL_OPS_BRIDGE_STATE_DIR` | `~/.hermes/kol-ops-bridge` | `inbound_poller.json`, `gmail_worker.json` |
| `KOC_DB_PATH` | `$HERMES_HOME/kol-ops-console/app.db` | Console DB for cross-process dedup |
| `KOL_OPS_GMAIL_INBOUND_AUTO_START` | `0` | Auto-enable inbound poller on bridge boot |
| `KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER` | unset | Disable inbound ticks (`running=false` in status) |
| `KOL_OPS_GMAIL_WORKER_PARALLEL` | `0` | Run inbound + SENT loops in parallel (escape hatch) |
| `KOL_OPS_GMAIL_WORKER_WAKE_SEC` | `5` | Coordinator wake interval |
| `KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT` | unset | `1` → rollback to `kol_reply_dispatcher_legacy.py` |
| `HERMES_GATEWAY_BASE` | `http://127.0.0.1:8642` | Gateway for agent dispatch |
| `HERMES_GATEWAY_KEY` | unset | Gateway auth (optional) |
| `KOL_OPS_INBOUND_DEBUG_LOG` | unset | Legacy script debug JSONL path (optional) |

## Rollback

Set `KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT=1` before starting the bridge or CLI.
The monolith lives at `scripts/kol_reply_dispatcher_legacy.py` (parity fixes
applied for facts unwrap, gateway retry, and incremental seen saves).

## Tests

```bash
cd hermes-agent/plugins/kol-ops-bridge
pytest tests/test_inbound_reply_*.py tests/test_gmail_inbound_poller.py -q
```
