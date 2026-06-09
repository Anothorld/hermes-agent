## Inbound poller runtime (pre-run for `kol-reply-dispatcher`)

The Hermes cron skill **does not** sweep Gmail itself. A bridge worker collects
unread replies, matches identities, and launches gateway runs with
`pending_replies[]` in the run input.

### Production (default)

1. Bridge `serve.py` runs `gmail_worker` (coordinator) or parallel inbound loop.
2. Worker calls `inbound_reply.orchestrator.run_once()` via **in-process**
   `cal.*` (no loopback HTTP).
3. Each new message triggers a gateway run with skill `kol-reply-dispatcher`.

Enable via Console **Gmail → Inbound poller** or
`POST /gmail/inbound-poller/configure {"enabled": true, "env": "LIVE"}`.

`KOL_OPS_GMAIL_INBOUND_AUTO_START=0` by default — poller stays off until
explicitly enabled.

### Debug CLI (operators / engineers only)

Thin wrapper — logic lives in `plugins/kol-ops-bridge/inbound_reply/`:

```bash
python3 plugins/kol-ops-bridge/scripts/kol_reply_dispatcher.py --env TEST
python3 plugins/kol-ops-bridge/scripts/kol_reply_dispatcher.py --env LIVE --watch --interval 60
```

Requires a running bridge (`HERMES_KOL_OPS_BRIDGE_BASE`) because the CLI uses
HTTP (`HttpBridgeAdapter`). **Agents must not invoke this script** for routine
cron work; use the bridge worker + Hermes cron skill instead.

One-shot bridge API: `POST /gmail/inbound-poller/run-once`.

### Failure / retry semantics

- Gateway or bridge infra failures → poller returns `retry` (message **not** marked
  seen). Next tick uses `should_retry_gateway_only` to re-dispatch without
  duplicating `kol_inbound_reply` events.
- Thread history fetch failures degrade to `thread_history=[]` and still dispatch.
- `GET /gmail/worker/status` exposes `inbound_disabled` when
  `KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER=1`.

### Rollback

`KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT=1` loads `kol_reply_dispatcher_legacy.py`
(monolith backup).

### Agent contract

- If gateway input has no `pending_replies` / empty array → exit immediately.
- Each item includes: `identity_id`, `campaign_id`, `env`, `latest_email`,
  `thread_history`, `anomaly_signals`, `chase_context`, dispatch-context snapshot.
- Do **not** re-fetch Gmail or re-run the poller from the LLM skill.
