# Bridge HTTP / CLI endpoints (kol-reply-dispatcher)

Use **`kol_bridge_tool.py`** only — never `curl` ad hoc, never `import dispatch_router`,
never `execute_code` for routing. Base URL: `HERMES_KOL_OPS_BRIDGE_BASE`
(default `http://127.0.0.1:8080/api/plugins/kol-ops-bridge`).

## Reads (GET)

| Purpose | CLI | HTTP |
|---------|-----|------|
| Dispatch bundle | `get-dispatch-context --identity-id ID --campaign-id CID --env LIVE` | `GET /identities/{id}/dispatch-context?campaign_id=&env=` |
| Poller idempotency | *(poller only)* | `GET /identities/{id}/reply-dispatch-status?campaign_id=&message_id=&env=` |
| Parsed escalation rules | `get-parsed-escalation-rules` | `GET /policies/escalation_rules/parsed` |

## Deterministic logic (POST `/logic/*`, no bridge key)

| Step | CLI subcommand | Body keys |
|------|----------------|-----------|
| Draftable goals | `select-draftable-plan --json '{...}'` | `goals`, `facts`, `signals`, `meta` |
| Escalation rules | `match-escalation-rules --json '{signals,...}'` | `signals`, optional `parsed` |
| Classifier sanitize preview | `sanitize-classifier-facts --json '{namespaces,signals}'` | `namespaces`, `signals` |

**404 on `/logic/select-draftable-plan`** → stop the run, log `bridge_stale_or_down`,
open escalation — do **not** import Python modules or reimplement routing in terminal.

## Mutations (require `X-Bridge-Key` + `--env`)

| Step | CLI | HTTP |
|------|-----|------|
| Classifier facts | `write-facts-multi --identity-id ID --json '{campaign_id,source,signals,namespaces}'` | `POST /facts/{id}/multi` |
| Fragment merge facts | same; `source=fragment-merge:<message_id>` | same |
| Persist draft | `persist-reply-draft --json '{...}'` | `POST /reply-drafts/persist` |
| Open escalation | `open-escalation --json '{reason,...}'` | `POST /escalations` |
| Mark handled | `mark-reply-handled --message-id MID --env LIVE` | `POST /gmail/mark-reply-handled` |
| Unmark (reprocess) | `unmark-reply-handled --message-id MID` | `POST /gmail/unmark-reply-handled` |

`write-facts-multi` with `source=email:<message_id>` must include classifier **`signals`**
(same turn) so the Bridge can sanitize premature committed keys.

`persist-reply-draft` with multiple `contributing` entries: set
`child_skill` to `kol-reply-synthesizer` (Bridge defaults this if omitted).

`mark-reply-handled`: `kol-outreach/pending-reply` is optional in Gmail; missing label
does not fail the call (handled label is still applied).
