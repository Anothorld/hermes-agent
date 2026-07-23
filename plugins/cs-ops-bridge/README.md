# CS Ops Bridge

State layer and watchers for Povison AI customer service. **Default Hermes profile: `povison-cs`** (see `profile_refs.py` / `CS_OPS_PROFILE`).

Gateway runs must use `hermes -p povison-cs gateway run` so agent skills, `.env`, and plugins load from `~/.hermes/profiles/povison-cs/`. The bridge stamps gateway `session_id` as `povison-cs:<env>:<quickcep_session_id>` to match.

## Quick start

```bash
# One-time: sync bridge key + CS_OPS vars into profile .env
python plugins/cs-ops-bridge/scripts/setup_cs_ops_env.py --write-profile-env

# Terminal 1 — bridge + watchers (or use playground/povison-cs-console/start.sh)
export FEISHU_APP_ID=...
export FEISHU_APP_SECRET=...
export QUICKCEP_EMAIL=...
export QUICKCEP_PASSWORD=...
export CS_OPS_GATEWAY_BASE=http://127.0.0.1:8643
python plugins/cs-ops-bridge/serve.py --port 8081

# Terminal 2 — povison-cs gateway (enable API_SERVER in profile config)
hermes -p povison-cs gateway run
```

## Profile config (`~/.hermes/profiles/povison-cs/config.yaml`)

```yaml
API_SERVER_ENABLED: true
API_SERVER_PORT: 8643
platform_toolsets:
  api_server:
    - browser
    - cronjob
    - file
    - memory
    - session_search
    - skills
    - terminal
    - todo
    - vision
    - web
```

Bridge-launched runs use the `api_server` platform. Without `platform_toolsets.api_server`, Hermes falls back to `hermes-api-server` (includes `execute_code` and `delegate_task`), which causes agents to bypass direct `terminal` calls. Apply the block above with:

```bash
python playground/povison-cs-console/scripts/ensure_cs_send_guard.py --profile povison-cs
```

Restart `hermes -p povison-cs gateway run` after changing toolsets.

## Env vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `HERMES_CS_OPS_BRIDGE_KEY` | — | Mutations auth |
| `CS_OPS_BRIDGE_BASE` | `http://127.0.0.1:8081/api/plugins/cs-ops-bridge` | CLI target |
| `CS_OPS_GATEWAY_BASE` | `http://127.0.0.1:8643` | Agent launch |
| `CS_OPS_PROFILE` | `povison-cs` | Hermes profile name (gateway `session_id` prefix) |
| `CS_OPS_PROFILE_DIR` | `~/.hermes/profiles/povison-cs` | Profile HERMES_HOME override (highest priority) |
| `HERMES_HOME` | profile dir when gateway runs | Used when `CS_OPS_PROFILE_DIR` unset; avoids wrong paths when agent `HOME` is `<profile>/home` |
| `CS_OPS_QUICKCEP_SKILL_DIR` | `<CS_OPS_PROFILE_DIR>/skills/social-media/quickcep` | CLI/SIO scripts |
| `CS_OPS_ENV` | `LIVE` | TEST/LIVE partition |
| `CS_OPS_GATEWAY_DRAIN` | `true` | Best-effort SSE drain after launch |
| `CS_OPS_GATEWAY_YOLO` | `1` | When set, bridge POST `/v1/runs` includes `"yolo": true` (skip routine approval prompts) |
| `CS_OPS_ESCALATION_TIMEOUT_AUTO_START` | `true` | SLA reminder worker |
| `CS_OPS_ESCALATION_TIMEOUT_INTERVAL_SEC` | `900` | SLA check interval |
| `CS_OPS_ESCALATION_TIMEOUT_HIGH_H` | `2` | High urgency SLA (hours) |
| `CS_OPS_ESCALATION_TIMEOUT_MED_H` | `8` | Medium urgency SLA |
| `CS_OPS_ESCALATION_TIMEOUT_LOW_H` | `24` | Low urgency SLA |
| `CS_OPS_ESCALATION_RESUMING_TIMEOUT_H` | `4` | Auto-close `resuming` escalations without handoff |
| `CS_OPS_PROCESSING_STALE_AUTO_START` | `true` | Recover orphaned `processing` sessions |
| `CS_OPS_PROCESSING_STALE_INTERVAL_SEC` | `120` | Stale processing scan interval |
| `CS_OPS_PROCESSING_STALE_MIN` | `120` | Minutes in `processing` without handoff → auto `failed` (default 2h; does **not** apply to `awaiting_expert`) |
| `CS_OPS_FEISHU_CHAT_PAGE_SIZE` | `50` | Feishu chat list page size |
| `CS_OPS_FEISHU_CHAT_LIST_MAX_PAGES` | `5` | Max pages when listing chat replies |
| `CS_OPS_FEISHU_THREAD_PAGE_SIZE` | `20` | Feishu topic thread page size |
| `CS_OPS_FEISHU_THREAD_LIST_MAX_PAGES` | `5` | Max pages when listing topic thread replies |
| `CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC` | `true` | When operator sends in QuickCEP, auto-close open `awaiting_answer` / `resuming` escalations for that session (`touch_session=false` for awaiting) |
| `CS_OPS_ESC_VAULT_DIR` | `<plugin>/data/esc_vault` | ESC attachment vault blob storage |
| `CS_OPS_ESC_VAULT_PUBLIC_BASE` | auto: `http://<LAN-IP>:8081/api/plugins/cs-ops-bridge` | Public base for expert upload links (Feishu); override for fixed hostname |
| `CS_OPS_ATTACHMENT_GUARD` | `1` | PDF attachment guard on `draft-save` (set `0` incident-only) |
| `CS_OPS_CLAIM_UPLOAD_BUDGET_SEC` | `60` | Max seconds for vault/Feishu CDN uploads on claim |

See [docs/povison-cs-escalation-vault.md](../../docs/povison-cs-escalation-vault.md) for vault API, PDF rules, and probe steps.

## Phase 2 safeguards

- **Busy session guard**: real customer follow-up while `processing` / `awaiting_expert` is recorded in CAL as `customer_followup_while_busy` (no duplicate QuickCEP internal notes). Gateway launch is skipped until the current cycle completes or stale recovery marks `failed`. **`apply-handoff --phase followup_while_busy` never posts QuickCEP notes** (CAL audit only). Repeat `awaiting_expert` / regressive `processing` handoffs on the same session also skip tags+notes. **Escalation after `draft_ready` in the same run** still syncs QuickCEP (`AI-待专家` replaces `AI-草稿待审`); lifecycle status is set by `apply-handoff awaiting_expert`, not `open-escalation`.
- **REST reconcile scope**: fallback poll only enqueues **new**, `pending`, or `failed` sessions. Busy rows are ignored so volatile `lastMsgTime` (including bumps from our own notes) cannot re-trigger follow-up loops. Dedup key is `rest:{lastMsgTime}` — never `lastMsgTime:unreadNum`.
- **Processing stale recovery**: if a session stays in `processing` longer than `CS_OPS_PROCESSING_STALE_MIN` (default **2 hours**; agent run died, gateway restart, terminal approval denied), background worker applies `failed` handoff so inbound can relaunch or operators can take over. **`awaiting_expert` is never touched** — those sessions wait on Feishu escalation reply (`feishu_escalation_poller` + escalation SLA timeout only).
- **Gateway launch**: retry on 429/502/503/504, in-process dedup, optional SSE drain (`gateway_launch.py`).
- **Escalation SLA**: `escalation_timeout.py` posts Feishu thread reminders and CAL events.
- **Feishu escalation notify**: `POST /escalations` auto-posts to **AI客服后援**. Shows **客户邮箱**, **📦 订单信息** (bridge auto-fetches QuickCEP `getOrderList` + Povison tracking when available), **客户来信摘要** (agent `--email-summary` in Simplified Chinese), and **原文引用** (agent `--email-quote` in the customer's original language). Summary/quote required when Feishu auto-send is on. **Custom body** (`--message` / `escalation_message`) sends the text verbatim — **no auto 📦 order block**, no required summary/quote validation; include order details manually if needed. **Idempotent retry guard**: when the agent times out on `open-escalation` after the bridge already delivered the Feishu message and retries, CAL dedups the escalation row AND the route skips the re-send (which would post a duplicate group message — the ESC:339/340 failure mode), returning the existing `feishu_message_id`/`thread_id` so the agent can still `apply-handoff --feishu-thread-id`. The guard only triggers when a `feishu_message_id` is already persisted; if the first send failed (nothing persisted) the retry still re-sends as a recovery path.
- **Feishu reply polling**: operators click **Reply** on the `[ESC:…]` post (no `@` needed). Poller lists `container_id_type=chat` with pagination and matches `parent_id` to `feishu_message_id`; topic groups (`omt_*`) use `container_id_type=thread`. **First reply wins** — atomic claim (`awaiting_answer` → `resuming`), thread lock notice `[ESC-LOCK:…]` (persisted as `feishu_lock_notified`, retried while `resuming` if send failed), later replies tracked and ignored. Console `console-reply` mirrors this: posts `[ESC-LOCK:…]` and persists the same flag so the poller doesn't double-post. Full operator text is kept in `resume_context.operator_answer_raw` for gateway resume (SQLite `operator_answer` column stays masked). On claim, **vault files + Feishu thread images** upload to QuickCEP CDN → `operator_attachments` + `allowed_attachment_urls`. Escalation message includes **Vault upload link** for PDF/multi-file. After agent `apply-handoff` reaches `draft_ready`/`failed`, bridge posts standalone `[ESC-DONE:…]`. `PATCH /escalations` on a `resuming` row routes through the same DONE path; `awaiting_answer` closes without DONE. Stale `resuming` rows auto-close via `CS_OPS_ESCALATION_RESUMING_TIMEOUT_H`.
- **PDF attachment guard**: `draft-save --attachments` with `.pdf` requires URL in session `allowed_attachment_urls` (vault path only). Product/static.povison PDFs blocked — text extraction in body instead. JPG/png unaffected. Disable: `CS_OPS_ATTACHMENT_GUARD=0`.
- **Agent guard**: enable plugin `cs-bridge-agent-guard` on the povison-cs gateway profile (`ensure_cs_send_guard.py`) to block direct `quickcep_cli`, `send-email`, and **`cs_bridge_tool` wrapped in `execute_code`**. Bridge steps must use **terminal** (one command per call).
- **Follow-up sub-session without `intentionTags`**: intent gate bypass when CAL already has another session row for the same customer email (`prior_customer_no_intent_tags`).
- **SIO token login**: watcher patches QuickCEP login env from profile `QUICKCEP_EMAIL` / `QUICKCEP_PASSWORD`, re-binds the SIO monitor module after import (avoids stale `from import get_valid_token`), and logs **WARNING** when cached JWT is invalid but re-login is skipped or fails.
- **Operator send when SIO down**: each REST poll runs `operator_send_reconcile` — scans `draft_ready` / `awaiting_expert` / `processing` rows for operator-sent messages and applies `operator_sent` handoff. Detection requires the **latest conversational message** to be `operator/html` (skips internal notes; ignores operator mail when a newer visitor mail exists). Skip-already is **cycle-aware**: only an `operator_sent` at or after the latest `inbound_received` / `customer_followup_while_busy` counts (prior-cycle sends after customer reopen do not block backfill). `quickcep_cli messages` failures log at **WARNING** (not silent debug). On successful handoff, open escalations (`awaiting_answer`, `resuming`) are resolved with `operator_manual_reply` unless `CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC=false`. A **repair sweep** in the same tick closes orphaned open escalations only when an `operator_sent` event exists **at or after that escalation's `created_at`** (covers prior close failures and deduped retries; does **not** treat a prior-cycle send after customer reopen as superseding a new escalation).
- **Graceful stack restart**: `playground/povison-cs-console/start.sh restart|stop` waits for CAL `processing` + escalation `resuming` before stopping bridge/gateway (see console README).
- **Gateway approvals**: bridge-launched runs POST `/v1/runs` with `"yolo": true` by default (`CS_OPS_GATEWAY_YOLO=1`) so routine Tirith warnings on terminal bridge calls do not stall automation. Hardline blocks (send-email, direct quickcep_cli, execute_code bridge batching) still apply. Profile uses `approvals.mode: smart`, omits `code_execution` from CLI toolsets, and pins `platform_toolsets.api_server` without `delegation` or `code_execution` so bridge steps use **terminal** directly.

## PII

Facts and event payloads are sanitized on write (`pii_sanitize.py`): emails, phones, card-like numbers, and street addresses are masked before SQLite persistence.

| `CS_OPS_HANDOFF_UNTRACKED_SENDS` | `false` | Apply handoff on operator send for untracked sessions |
| `CS_OPS_HANDOFF_SKIP_QUICKCEP` | — | Skip QuickCEP tag/note writes (CAL events only) |
| `CS_OPS_INTENT_FILTER` | `true` | Only auto-process sessions whose QuickCEP `intentionTags` include allowed business intents |
| `CS_OPS_ALLOWED_INTENTION_TAGS` | `产品咨询,物流咨询` | Comma-separated QuickCEP AI intent labels (overrides `config/intent_filter.yaml`) |
| `CS_OPS_INTENT_FETCH_MAX_PAGES` | `5` | Session-list pages to scan when resolving `intentionTags` for one session |

## Inbound intent filter

QuickCEP auto-classifies each email session into **业务意图** labels stored in `intentionTags` (not manual session tags). The watcher **only launches AI** when at least one tag matches the allowlist (default: **产品咨询**, **物流咨询**).

### Intent classifier seam (`CS_INTENT_ENABLED`)

When the standalone [`cs-intent-classifier`](../cs-intent-classifier/README.md) plugin is enabled (`CS_INTENT_ENABLED=true`), the gate delegates to it instead of relying on QuickCEP tags:

- `intent_gate.py` pre-fetches the full email body + dispatch-context (orders + shipping address), calls `POST /classify` on the classifier, and gates on the returned `gate_extract.in_scope`. The pre-fetch selects the latest **`ownerType=visitor`** message from `get-messages` — QuickCEP inserts system rows (`chat_start`, `ruleAssignHumanQueue`, `assignChat`) after the customer email, so blindly taking `messages[-1]` feeds the system assignment notice to the LLM and causes real customer emails to be misclassified as `spam_irrelevant`. When no visitor row exists the body is `None` and the seam falls through to the legacy gate (no system noise is ever classified).
- `bridge_agent_contract.py` injects a `# gate_extract` block into the agent brief (multi-intent, emotion, language, region, uncertain-field confirmation rules). The agent skips the legacy `classify-intent` step.
- **Off by default** — `CS_INTENT_ENABLED=false` preserves today's QuickCEP-tag behavior with zero regression. The classifier service does not even need to be running.
- **Graceful degradation** — if the classifier is unreachable, the seam falls back to the legacy QuickCEP-tag gate so inbound is never blocked by a classifier outage.
- **Idempotency cache** — before each `POST /classify`, the seam does a `GET /gate-extract/{session_id}` on the classifier. If a result already exists for the session (e.g. a prior call succeeded server-side but the HTTP client timed out reading the response), the cached result is reused and the expensive LLM call is skipped. This prevents unbounded duplicate LLM calls when the LLM endpoint is slow: without the cache, a client-side timeout → graceful fallback → transient skip (no CAL dedup) → next REST tick re-POSTs → server runs LLM again → wasted call, repeating every poll. The `urllib` timeout is aligned to `CS_INTENT_SEAM_TIMEOUT` (default 45s, ≥ the 30s LLM timeout) so the client never abandons a still-running LLM call.
- **Conversation context** (`CS_INTENT_CONTEXT_TURNS`, default 3) — the seam extracts recent prior messages from the same thread (visitor + operator, excluding system/bot/internal-note/phone-call records) and passes them as `conversation_history` to the classifier. This lets the LLM understand reply context (e.g., agent said "order ships July 10" → customer replies "ok but change address" → intent is `order_management`, not spam). Quoted reply content (`On ... wrote:`, `-----Original Message-----`, `>` lines, forwarded blocks) is stripped from each history message. The classified email is always the latest visitor email (`contentType` ≠ `call`); history is context only. Set `CS_INTENT_CONTEXT_TURNS=1` to disable.

See `plugins/cs-intent-classifier/README.md` for the classifier's self-contained DB, LLM config, and learning loop.

## Email-only channel scope

Automation listens to and processes **QuickCEP email sessions only**. Web chat, SMS, phone, and other channels are ignored at every entry point:

- SIO `visitorSendMsg` — profile monitor already filters `channel=email`; bridge `_launch_for_message` rejects non-email payloads.
- REST reconcile — `quickcep_cli sessions --email-only`; each row is checked via `email_channel.inbound_payload_is_email`.
- Console `POST /sessions/{id}/relaunch` — verifies session is email: CAL row with `customer_email` (instant, for aged sessions), else QuickCEP email list, else messages API channel probe (`409 non_email_channel` otherwise).
- Operator send handoff — SIO patch handles `operatorSendMsg` with `channel=email` only.

Shared module: `email_channel.py` (`is_email_channel`, `inbound_payload_is_email`, `cal_session_is_email`, `session_is_email`).

Permanent inbound skips (PR3): `non_email` / `internal_email_blocklist` / `intent_gate: intention_not_allowed` / `ad` enqueue into CAL with `status=skipped` + an `inbound_skipped` event (payload `gate` field). Transient skips (`intent_gate: no_intention_tags` / `assigned_to_operators`) stay log-only to preserve REST reconcile retry — enqueuing would write `cs_message_dedup` and block later re-evaluation.

- SIO `visitorSendMsg` and REST reconcile both pass through `intent_gate.check_intent_gate` before enqueue/launch.
- Sessions with no `intentionTags` are skipped unless CAL already has another session for the same customer email (follow-up threads that QuickCEP opens as a new sub-session often lack AI intent tags).
- Other sessions with no tags yet (classification pending) are skipped; REST reconcile retries on the next poll once QuickCEP assigns tags.
- REST inbound launch only auto-enqueues CAL rows in `pending` / `failed` (avoids loops when internal notes bump `lastMsgTime`). **New QuickCEP sub-sessions** (no CAL row) are always eligible; follow-up on the **same** closed session still depends on SIO or manual relaunch.
- REST uses `--unread-only`; read follow-ups on an existing CAL row are not re-polled unless SIO delivers the event.
- Disable for debugging: `CS_OPS_INTENT_FILTER=false`.
- Config file: `config/intent_filter.yaml`.

## Resume failure detection + manual retry

When a resume agent run ends without calling `apply-handoff` (the ESC36/37 failure mode — model produced gibberish and treated it as completion), the escalation stays stuck in `resuming` with no draft. The bridge now detects this immediately and notifies the operator:

1. **Detection** — the `cs-bridge-agent-guard` gateway plugin registers an `on_session_end` hook that fire-and-forgets an HTTP POST to `POST /internal/run-finished` on the bridge whenever any CS agent run ends. The bridge checks whether a `resuming` escalation still exists for that session (handoff not applied). A false-positive guard queries `GET /v1/runs/{resume_run_id}` to confirm the resume run itself has terminated (not a concurrent `operator_edit_memory` run).

2. **Notification** — on confirmed failure, the bridge writes a `escalation_resume_failed` CAL event, posts `[ESC-FAILED:{id}]` to the Feishu escalation thread (skipped for console-only escalations with no Feishu thread), and sets `resume_failed_notified` in `resume_context` (idempotent — duplicate hook callbacks are no-ops).

3. **Manual retry** — the operator clicks the existing Console「重新生成」button. `POST /sessions/{id}/relaunch` now auto-routes: if the session has an escalation with a recorded expert answer, the call reopens the escalation (`reopen_escalation_for_resume` — clears `resume_run_id` + failure markers, resets the 4h timeout anchor) and relaunches `# escalation_resume` with the original `operator_answer_raw`. The response includes `kind: "resume_retry"` so the frontend toast distinguishes it from a normal inbound relaunch. Sessions without an expert answer fall through to the original inbound relaunch (and the `awaiting_expert` 409 guard still applies).

4. **Feishu retry tags** — `[ESC-DONE:{id}]（重试）` and `[ESC-FAILED:{id}]（重试后）` distinguish retry outcomes from first-attempt messages.

**Key safety constraints:**
- `reopen` resets `resume_launched_at=now` so the 4h `escalation_timeout` doesn't immediately re-close the reopened escalation.
- `operator_replied`/`reviewed`/`operator_sent` sessions skip resume retry (status whitelist guard) to avoid overwriting already-sent customer replies.
- Resume retry does not change session status — the agent's `apply-handoff` drives lifecycle transitions naturally.
- Gateway hook env vars (`CS_OPS_BRIDGE_BASE`, `HERMES_CS_OPS_BRIDGE_KEY`) must be set in the povison-cs profile `.env`; missing vars degrade gracefully (log + fall back to 4h timeout).

## Session handoff (tags + internal notes)

Deterministic lifecycle labeling via `session_handoff.py`:

```bash
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py apply-handoff \
  --env LIVE --session-id <quickcep_session_id> \
  --phase draft_ready \
  --customer-need "客户咨询物流进度" \
  --actions-taken "已查询订单并保存回复草稿" \
  --operator-hint "草稿待核对后发送"
```

**Note language:** Internal QuickCEP notes (all `apply-handoff` text fields and bridge-composed defaults) are **Simplified Chinese only**, written for **customer-service operators** — session business (customer need, actions taken, next steps). No CLI flags, system component names, or log/debug hints. Bridge sanitizes common technical tokens. Customer email drafts (`draft-save --content` / `--content-file`) remain English.

**Dollar amounts:** In shell, `$200` inside double-quoted `--content "..."` becomes `00` (bash expands `$2`). Use `--content-file` or single-quoted `--content '...$200...'`.

Phases: `processing`, `draft_ready`, `awaiting_expert`, `failed`, `skipped`, `reviewed`, `followup_while_busy`, `operator_sent`.

**Intentional skip (`skipped`):** B2B spam, carrier COI misroute, SEO pitches, and other out-of-scope mail must use `--phase skipped` (QuickCEP tag **AI-已结案**, CAL `status=skipped`). Do **not** use `--phase failed` for these — operators misread AI-处理失败 as a system crash. If an agent still sends `--phase failed` with skip-like wording (B2B/垃圾/承运商/误入), bridge auto-remaps to `skipped`.

**Draft save:** `cs_bridge_tool draft-save` supports optional `--attachments` (JSON array, forwarded to QuickCEP). **PDF guard** blocks non-vault PDFs when `--attachments` contains `.pdf` (see `config/attachment_guard.yaml`). `upload-file` subcommand wraps QuickCEP CDN upload. Agents must use `cs_bridge_tool` only — not raw `quickcep_cli`. The **default** draft-save path writes to CAL (no QuickCEP `joinChat`); the **legacy** path (`--legacy-quickcep-draft` or `CS_OPS_DRAFT_SAVE_LEGACY_QUICKCEP=1`) writes to QuickCEP and calls `joinChat` first:

| Step | Timeout | Retry |
|------|---------|-------|
| JWT check `getUserInfo` | 45s | — |
| `joinChat` | 60s | up to 2 retries (2s / 4s backoff) on HTTP timeout only |

On failure, JSON includes `failed_step` (`getUserInfo` or `joinChat`) and `error_detail` for operators. Subprocess budget per attempt: 130s.

**Launch joinChat:** when an inbound email passes all gates and the session enters `processing`, the watcher (and Console `relaunch`) call QuickCEP `joinChat` **before** the gateway launch — the AI account becomes visible to operators in QuickCEP during the lookup/draft phase. This is **fail-soft**: join failure logs a WARNING + writes a `quickcep_join_chat` CAL event but does **not** block the agent run; Console `send-email` still joins as a fallback. Default **1 attempt** (~60s) to avoid stalling the inbound path.

| Env | Default | Purpose |
|-----|---------|---------|
| `CS_OPS_JOIN_CHAT_ON_LAUNCH` | `1` | `0`/`false` disables launch/relaunch join (rollback switch) |
| `CS_OPS_LAUNCH_JOIN_MAX_ATTEMPTS` | `1` | Override attempt count for launch/relaunch join |

**Draft HTML:** `draft-save` normalizes plain text and fake `<html><body>` wrappers into `<p>` / `<br>` so QuickCEP shows paragraph breaks. Prefer plain English in `--content-file`; avoid Markdown-only bold unless `**text**` (converted to `<strong>`).

**Concurrent run safety:** shared `/tmp/draft.html` is now rejected by `cs_bridge_tool draft-save` because concurrent sessions can overwrite each other. Use session-scoped files (for example `/tmp/draft-<quickcep_session_id>.html`).

**Dispatch-context tracking prefill:** when `intentionTags` includes `物流咨询` and `orders` is non-empty, bridge adds `tracking` summaries in `get-dispatch-context` (status, tracking number, EDD window) via Povison order-track API. This reduces model hallucination and lets logistics replies start from deterministic data.

**Operator send:** SIO `operatorSendMsg` → automatic `operator_sent` handoff (tags + post-send note). Open escalations for the session are auto-resolved (`operator_manual_reply`; `resuming` stops the gateway resume run when possible, posts Feishu `[ESC-DONE:…]` with「客服已直接回复」). Deduped SIO events still attempt ESC close when open rows remain. SIO login uses `QUICKCEP_EMAIL` / `QUICKCEP_PASSWORD` from profile `.env` (same as `quickcep_cli`). When SIO is unavailable, each REST poll also runs `operator_send_reconcile` to backfill `operator_sent` for CAL rows still in `draft_ready` / `awaiting_expert` / `processing` when QuickCEP message history shows the latest outbound is operator email, then **repair** orphaned open escalations.

**Tag map:** `config/session_tag_map.yaml` — run `scripts/sync_session_tags.py` after creating **AI客服** tags in QuickCEP admin (`AI-处理中`, `AI-草稿待审`, `AI-待专家`, `AI-处理失败`, `AI-已结案`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/sessions/{id}/handoff` | key | Apply lifecycle tags + internal note |

## Console API (Phase 3)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/sessions` | — | List/search sessions (`limit`, `offset`, `total`, `has_more`; supports `since`/`until` server-side date-window filter on `COALESCE(processing_started_at, created_at)`) |
| GET | `/daily-report/stats` | — | One-shot daily-report aggregate: processed sessions in window + escalation count by `created_at` + `draft_saved` event session-id set (schema v5+) |
| GET | `/escalations/{id}/upload-link` | key | Signed upload URL (Feishu backfill) |
| POST | `/escalations/{id}/feishu-upload-link` | key | Reply upload link on Feishu thread |
| GET | `/escalations/{id}/vault` | key | List vault files |
| GET | `/escalations/{id}/upload` | token | Expert upload page |
| POST | `/escalations/{id}/vault` | token | Expert upload file |
| GET | `/sessions/{id}/attachment-guard-context` | — | PDF guard allow list for draft-save |
| POST | `/escalations/{id}/resume` | key | Launch gateway + resolve |
| POST | `/sessions/{id}/relaunch` | key | Retry failed/stuck session (auto-routes to resume retry when an escalation with expert answer exists) |
| POST | `/internal/run-finished` | key | Gateway `on_session_end` callback for resume failure detection |
| POST | `/sessions/{id}/close` | key | QuickCEP `leave-chat` + optional CAL `reviewed` (Console **结束工单**). Body `close_escalations=true` (垃圾/无关关闭流程) 同时 resolve 该会话所有 open 升级 |
| POST | `/sessions/unassign-all` | key | **下班** flow: uses operator's QuickCEP credentials to list sessions assigned to them and call `batchLeaveChat` for each. Token-cache safe (`--token` passthrough, no AI account cache overwrite). |
| POST | `/admin/pause` | key | **下班** global pause: sets a flag in `cs_poller_state` so `quickcep_watcher._launch_for_message` skips ALL new AI launches (SIO + REST). In-flight runs complete naturally. Prevents new drafts → new escalations on off-hours. |
| POST | `/admin/resume` | key | **开工** global resume: clears the pause flag so new AI launches resume. |
| GET | `/admin/pause-status` | — | Read-only: returns the current global pause flag (no key required). |

**Close confirmation:** Email sessions record `leaveChat` in message history; live chat uses `chat_end`. Bridge `close_session.py` and profile `quickcep_cli leave-chat` accept both; legacy CLI-only `chat_end` checks caused false `chat_end_not_confirmed`.

**Spam/irrelevant close:** When the operator clicks "关闭工单" on a session whose primary intent is outside AI processing scope, the Console passes `close_escalations=true`; `close_session` then calls `close_escalations_on_operator_manual_reply` to resolve any `awaiting_answer`/`resuming` escalations for that session, so a single action fully tears down the ticket. Result includes `escalations_closed: [{escalation_id, ok, prior_state}]`. **Fallback:** if `close_escalations` is missing/false but the close `note` contains `不在处理范围` / `垃圾/无关`, bridge still resolves open escalations (guards against console version skew). Regular **结束工单** also passes `close_escalations=true`.

### L2 live caches (Workbench read path)

The Console Workbench's L2 live endpoints (`/messages`, `/tags`, `/orders`) hit QuickCEP via `quickcep_cli` and are wrapped with short-lived in-process caches so the FE can poll without hammering the platform. All caches are keyed by `quickcep_session_id` and live in `quickcep_live.py`.

| Endpoint | TTL | Notes |
|----------|-----|-------|
| `GET /sessions/{id}/messages` | **15s** (page 0 only) | Page 0 (`page=0`, the newest page) with `since=None` is cached; `since` filtering is applied in-memory. When `since` is absent from the cached page the call falls back to a fresh CLI fetch (never silently drops newer messages). **Older pages (`page>0`) bypass the cache entirely** — historical messages are stable, requested only on scroll-up, and caching them would risk serving a stale shifted page during `pageIndex` drift. Errors are not cached. |
| `GET /sessions/{id}/tags` | 300s | tagIds reverse-resolved to names via the session tag map. |
| `GET /sessions/{id}/orders` | 60s | Reuses `cal._fetch_visitor_orders`. |

**Message pagination & drift handling:** `GET /messages` accepts `page` (0-based `pageIndex`, page 0 = newest) and `page_size` (default 10, max 100). QuickCEP paginates by offset in createTime-DESC order, so when new messages arrive page 0's content shifts down into page 1 — a naive prepend of page 1 would duplicate already-loaded messages. The Console FE dedups by QuickCEP message `id` (notes/systems by `when+kind+text` fingerprint) and, on a full-overlap page (0 new ids but `loadedCount < total`), continues to the next page (capped at 3 continuation pages) so older messages are never silently missed. A page returning 0 records is the hard stop. `since=<message_id>` returns only messages after that id and is used for 5s incremental polling instead of re-fetching the whole page 0.

**Cache invalidation trigger points** (call `quickcep_live.invalidate_cache(session_id)`):

| Trigger | Where | Why |
|---------|-------|-----|
| `POST /sessions/{id}/note` | `plugin_api.add_session_note` | Note add bumps session activity. |
| `POST /sessions/{id}/send-reply` | `send_reply.send_reply` (success path) | New outbound changes messages/tags/lifecycle. |
| Inbound watcher event | `quickcep_watcher._launch_for_message` (non-deduped) | New visitor message must be visible on next GET. |

`send_reply` also passes `force_fresh=True` to `fetch_messages` when backfilling the outbound `message_id`, so the just-sent message is visible immediately without waiting for TTL expiry.

Watcher runs in-process alongside the bridge HTTP server (`serve.py` lifespan), so the dict invalidation is visible to API routes immediately — no cross-process coordination needed.

### HTTP cache headers on read-only GET

The following read-only routes set `Cache-Control` so the browser can serve repeated clicks from its cache without re-hitting the bridge. `max-age` is always `≤` the backend cache TTL, so the browser never serves data older than the bridge would.

| Route | `Cache-Control` |
|-------|-----------------|
| `GET /sessions/{id}/state` | `public, max-age=2` |
| `GET /sessions/{id}/workbench` | `private, max-age=2` |
| `GET /sessions/{id}/messages` (page 0) | `private, max-age=5, stale-while-revalidate=10` |
| `GET /sessions/{id}/messages` (page > 0) | `private, max-age=60` (older pages are stable, not bridge-cached) |
| `GET /sessions/{id}/tags` | `private, max-age=30, stale-while-revalidate=270` |
| `GET /sessions/{id}/orders` | `private, max-age=30, stale-while-revalidate=30` |
| `GET /sessions` | `private, max-age=3` |

Mutation routes (POST/PUT/DELETE) intentionally do **not** set `Cache-Control`.

Operator UI: `playground/povison-cs-console/` (port 8092).

## CLI

```bash
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py health
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py get-dispatch-context --env LIVE --session-id <id>
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py get-messages --env LIVE --session-id <id>
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py classify-intent --env LIVE --subject "..." --body "..."
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py apply-handoff --env LIVE --session-id <id> --phase draft_ready --operator-hint "..."
```

`apply-handoff --phase` must be one of: `processing`, `draft_ready`, `awaiting_expert`, `failed`, `skipped`, `reviewed`, `followup_while_busy`, `operator_sent`. Unknown phases return HTTP 422/400 (not 500). Common aliases `completed` / `processed_by_human` normalize to `reviewed`. Use `skipped` for intentional no-reply (B2B/spam/carrier misroute); bridge auto-remaps mistaken `failed` handoffs when skip wording is detected.

```bash
python plugins/cs-ops-bridge/scripts/sync_session_tags.py   # after QuickCEP AI tags created
```

## Architecture

See [docs/povison-cs-architecture.md](../../docs/povison-cs-architecture.md).

## Skills

- `povison-cs-orchestrator-flow` — inbound processing
- `povison-cs-escalation-resumer` — post-Feishu resume
- `povison-feishu-escalation` — escalation message templates
- `povison-cs-daily-reporting` — daily performance report (lives in the `povison-cs` profile; reads `/daily-report/stats`)

## CAL schema version

Current: **v5**. The `cs_session` table carries:

- `processing_started_at` (v5) — stamped once when a session first leaves `pending`, never overwritten. The daily report buckets sessions by this timestamp (with `created_at` fallback for pre-v5 rows) instead of the volatile `updated_at`, so cross-day follow-ups no longer migrate a session across daily buckets. Migration is non-backfilling by design.
- `sent_draft_html` / `sent_draft_source` / `sent_draft_at` (v4) — snapshot of the AI draft taken at `clear_draft()` time so the daily report can compute adoption rates after the composer is cleared.
- `draft_*` (v3) — current draft state.

The `/daily-report/stats` endpoint bundles the three reads the daily report needs (processed sessions in window, escalation count by `created_at`, `draft_saved` event session-id set) into one consistent snapshot — see `povison-cs-daily-reporting/SKILL.md` for the report-side contract.
