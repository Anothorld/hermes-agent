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

## Phase 2 safeguards

- **Busy session guard**: real customer follow-up while `processing` / `awaiting_expert` is recorded in CAL as `customer_followup_while_busy` (no duplicate QuickCEP internal notes). Gateway launch is skipped until the current cycle completes or stale recovery marks `failed`. **`apply-handoff --phase followup_while_busy` never posts QuickCEP notes** (CAL audit only). Repeat `awaiting_expert` / regressive `processing` handoffs on the same session also skip tags+notes. **Escalation after `draft_ready` in the same run** still syncs QuickCEP (`AI-待专家` replaces `AI-草稿待审`); lifecycle status is set by `apply-handoff awaiting_expert`, not `open-escalation`.
- **REST reconcile scope**: fallback poll only enqueues **new**, `pending`, or `failed` sessions. Busy rows are ignored so volatile `lastMsgTime` (including bumps from our own notes) cannot re-trigger follow-up loops. Dedup key is `rest:{lastMsgTime}` — never `lastMsgTime:unreadNum`.
- **Processing stale recovery**: if a session stays in `processing` longer than `CS_OPS_PROCESSING_STALE_MIN` (default **2 hours**; agent run died, gateway restart, terminal approval denied), background worker applies `failed` handoff so inbound can relaunch or operators can take over. **`awaiting_expert` is never touched** — those sessions wait on Feishu escalation reply (`feishu_escalation_poller` + escalation SLA timeout only).
- **Gateway launch**: retry on 429/502/503/504, in-process dedup, optional SSE drain (`gateway_launch.py`).
- **Escalation SLA**: `escalation_timeout.py` posts Feishu thread reminders and CAL events.
- **Feishu escalation notify**: `POST /escalations` auto-posts to **AI客服后援**. Shows **客户邮箱**, **📦 订单信息** (bridge auto-fetches QuickCEP `getOrderList` + Povison tracking when available), **客户来信摘要** (agent `--email-summary` in Simplified Chinese), and **原文引用** (agent `--email-quote` in the customer's original language). Summary/quote required when Feishu auto-send is on. **Custom body** (`--message` / `escalation_message`) sends the text verbatim — **no auto 📦 order block**, no required summary/quote validation; include order details manually if needed.
- **Feishu reply polling**: operators click **Reply** on the `[ESC:…]` post (no `@` needed). Poller lists `container_id_type=chat` with pagination and matches `parent_id` to `feishu_message_id`; topic groups (`omt_*`) use `container_id_type=thread`. **First reply wins** — atomic claim (`awaiting_answer` → `resuming`), thread lock notice `[ESC-LOCK:…]` (persisted as `feishu_lock_notified`, retried while `resuming` if send failed), later replies tracked and ignored. Full operator text is kept in `resume_context.operator_answer_raw` for gateway resume (SQLite `operator_answer` column stays masked). After agent `apply-handoff` reaches `draft_ready`/`failed`, bridge posts standalone `[ESC-DONE:…]`. `PATCH /escalations` on a `resuming` row routes through the same DONE path; `awaiting_answer` closes without DONE. Stale `resuming` rows auto-close via `CS_OPS_ESCALATION_RESUMING_TIMEOUT_H`.
- **Agent guard**: enable plugin `cs-bridge-agent-guard` on the povison-cs gateway profile (`ensure_cs_send_guard.py`) to block direct `quickcep_cli`, `send-email`, and **`cs_bridge_tool` wrapped in `execute_code`**. Bridge steps must use **terminal** (one command per call).
- **Follow-up sub-session without `intentionTags`**: intent gate bypass when CAL already has another session row for the same customer email (`prior_customer_no_intent_tags`).
- **SIO token login**: watcher patches QuickCEP login env from profile `QUICKCEP_EMAIL` / `QUICKCEP_PASSWORD`, re-binds the SIO monitor module after import (avoids stale `from import get_valid_token`), and logs **WARNING** when cached JWT is invalid but re-login is skipped or fails.
- **Operator send when SIO down**: each REST poll runs `operator_send_reconcile` — scans `draft_ready` / `awaiting_expert` / `processing` rows for operator-sent messages and applies `operator_sent` handoff. `quickcep_cli messages` failures log at **WARNING** (not silent debug).
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

## Email-only channel scope

Automation listens to and processes **QuickCEP email sessions only**. Web chat, SMS, phone, and other channels are ignored at every entry point:

- SIO `visitorSendMsg` — profile monitor already filters `channel=email`; bridge `_launch_for_message` rejects non-email payloads.
- REST reconcile — `quickcep_cli sessions --email-only`; each row is checked via `email_channel.inbound_payload_is_email`.
- Console `POST /sessions/{id}/relaunch` — verifies session is email via QuickCEP API (`409 non_email_channel` otherwise).
- Operator send handoff — SIO patch handles `operatorSendMsg` with `channel=email` only.

Shared module: `email_channel.py` (`is_email_channel`, `inbound_payload_is_email`, `session_is_email`).

- SIO `visitorSendMsg` and REST reconcile both pass through `intent_gate.check_intent_gate` before enqueue/launch.
- Sessions with no `intentionTags` are skipped unless CAL already has another session for the same customer email (follow-up threads that QuickCEP opens as a new sub-session often lack AI intent tags).
- Other sessions with no tags yet (classification pending) are skipped; REST reconcile retries on the next poll once QuickCEP assigns tags.
- REST inbound launch only auto-enqueues CAL rows in `pending` / `failed` (avoids loops when internal notes bump `lastMsgTime`). **New QuickCEP sub-sessions** (no CAL row) are always eligible; follow-up on the **same** closed session still depends on SIO or manual relaunch.
- REST uses `--unread-only`; read follow-ups on an existing CAL row are not re-polled unless SIO delivers the event.
- Disable for debugging: `CS_OPS_INTENT_FILTER=false`.
- Config file: `config/intent_filter.yaml`.

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

Phases: `processing`, `draft_ready`, `awaiting_expert`, `failed`, `reviewed`, `followup_while_busy`, `operator_sent`.

**Draft save:** `cs_bridge_tool draft-save` supports optional `--attachments` (JSON array, forwarded to QuickCEP). Agents must use `cs_bridge_tool` only — not raw `quickcep_cli`. Before writing a draft it calls QuickCEP `joinChat` automatically:

| Step | Timeout | Retry |
|------|---------|-------|
| JWT check `getUserInfo` | 45s | — |
| `joinChat` | 60s | up to 2 retries (2s / 4s backoff) on HTTP timeout only |

On failure, JSON includes `failed_step` (`getUserInfo` or `joinChat`) and `error_detail` for operators. Subprocess budget per attempt: 130s.

**Draft HTML:** `draft-save` normalizes plain text and fake `<html><body>` wrappers into `<p>` / `<br>` so QuickCEP shows paragraph breaks. Prefer plain English in `--content-file`; avoid Markdown-only bold unless `**text**` (converted to `<strong>`).

**Concurrent run safety:** shared `/tmp/draft.html` is now rejected by `cs_bridge_tool draft-save` because concurrent sessions can overwrite each other. Use session-scoped files (for example `/tmp/draft-<quickcep_session_id>.html`).

**Dispatch-context tracking prefill:** when `intentionTags` includes `物流咨询` and `orders` is non-empty, bridge adds `tracking` summaries in `get-dispatch-context` (status, tracking number, EDD window) via Povison order-track API. This reduces model hallucination and lets logistics replies start from deterministic data.

**Operator send:** SIO `operatorSendMsg` → automatic `operator_sent` handoff (tags + post-send note). SIO login uses `QUICKCEP_EMAIL` / `QUICKCEP_PASSWORD` from profile `.env` (same as `quickcep_cli`). When SIO is unavailable, each REST poll also runs `operator_send_reconcile` to backfill `operator_sent` for CAL rows still in `draft_ready` / `awaiting_expert` / `processing` when QuickCEP message history shows an operator outbound.

**Tag map:** `config/session_tag_map.yaml` — run `scripts/sync_session_tags.py` after creating **AI客服** tags in QuickCEP admin (`AI-处理中`, `AI-草稿待审`, `AI-待专家`, `AI-处理失败`, `AI-已结案`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/sessions/{id}/handoff` | key | Apply lifecycle tags + internal note |

## Console API (Phase 3)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/sessions` | — | List/search sessions |
| POST | `/escalations/{id}/resume` | key | Launch gateway + resolve |
| POST | `/sessions/{id}/relaunch` | key | Retry failed/stuck session |

Operator UI: `playground/povison-cs-console/` (port 8092).

## CLI

```bash
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py health
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py get-dispatch-context --env LIVE --session-id <id>
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py get-messages --env LIVE --session-id <id>
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py classify-intent --env LIVE --subject "..." --body "..."
python plugins/cs-ops-bridge/scripts/cs_bridge_tool.py apply-handoff --env LIVE --session-id <id> --phase draft_ready --operator-hint "..."
```

`apply-handoff --phase` must be one of: `processing`, `draft_ready`, `awaiting_expert`, `failed`, `reviewed`, `followup_while_busy`, `operator_sent`. Unknown phases return HTTP 422/400 (not 500). Common aliases `completed` / `processed_by_human` normalize to `reviewed`.

```bash
python plugins/cs-ops-bridge/scripts/sync_session_tags.py   # after QuickCEP AI tags created
```

## Architecture

See [docs/povison-cs-architecture.md](../../docs/povison-cs-architecture.md).

## Skills

- `povison-cs-orchestrator-flow` — inbound processing
- `povison-cs-escalation-resumer` — post-Feishu resume
- `povison-feishu-escalation` — escalation message templates
