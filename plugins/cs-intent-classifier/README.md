# cs-intent-classifier

Independent, switchable intent/emotion/region classifier for Povison CS inbound.

## What it does

Classifies inbound customer emails into a structured `gate_extract` JSON: multi-intent (five-class taxonomy), emotion, language, customer region (with reliability source), referenced products/orders, urgency, threat signals, and a Chinese summary. Outputs a `fabrication_guard` self-assertion and lists uncertain/null fields so downstream agents never act on fabricated data.

Self-contained learning loop: operator corrections (from the Console) feed a T1 error bank, T2 few-shot injection, and T3 weekly offline policy distillation that auto-promotes when eval passes — no human approve step, fully autonomous, with daily pass-rate trend driving rebuild-vs-repair decisions.

## Independence

This plugin is **decoupled from cs-ops-bridge and the povison-cs profile**:

- Own SQLite DB (`data/cs_intent.db`) — does not write cs-ops-bridge CAL.
- Own HTTP API (port 8082, `CS_INTENT_PORT`).
- Own LLM config (`CS_INTENT_LLM_*` env vars) — does not read profile `config.yaml` / `auxiliary`.
- Own cron jobs (eval_daily, optimize_distill, optimize_fewshot).
- Enabled in cs-ops-bridge only via `CS_INTENT_ENABLED=true`; **off by default = zero behavior change**.

The only coupling points are two seams in cs-ops-bridge, both behind the switch:
1. `intent_gate.py` — gates on `gate_extract.in_scope` when enabled; legacy QuickCEP `intentionTags` logic when disabled.
2. `bridge_agent_contract.py` — injects a `# gate_extract` block into the agent brief when enabled; no block when disabled.

The Console frontend is the one allowed coupling: it calls `PATCH /intent/{id}` for corrections and `GET /learning/*` for the effect panel.

## Run

```bash
# Configure LLM (self-configured — independent of profile)
export CS_INTENT_LLM_PROVIDER=openai
export CS_INTENT_LLM_MODEL=gpt-4o-mini      # or your chosen small model
export CS_INTENT_LLM_API_KEY=sk-...
# Optional: custom OpenAI-compatible base URL
# export CS_INTENT_LLM_BASE_URL=https://api.openai.com/v1

# Start the service
python plugins/cs-intent-classifier/serve.py --host 127.0.0.1 --port 8082

# Enable in cs-ops-bridge (seams activate)
export CS_INTENT_ENABLED=true
```

Without an LLM configured, the classifier runs keyword-only and returns a conservative `review` gate_extract for ambiguous cases (no fabrication, no false pass/block).

## Env vars

| Var | Default | Purpose |
|-----|---------|---------|
| `CS_INTENT_ENABLED` | `false` | Master switch. cs-ops-bridge seams read this. |
| `CS_INTENT_PORT` | `8082` | HTTP listen port. |
| `CS_INTENT_BASE_URL` | `http://127.0.0.1:8082` | Where seams / Console find the service. |
| `CS_INTENT_DB_PATH` | plugin-local `data/cs_intent.db` | SQLite location. |
| `CS_INTENT_LLM_PROVIDER` | (empty) | LLM provider tag. |
| `CS_INTENT_LLM_MODEL` | (empty) | Model name. |
| `CS_INTENT_LLM_API_KEY` | (falls back to `OPENAI_API_KEY`) | LLM key. |
| `CS_INTENT_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. |
| `CS_INTENT_CONTEXT_TURNS` | `3` | Number of recent messages (visitor+operator) to include as conversation context. Set `1` to disable history. Read by the cs-ops-bridge seam. |
| `CS_INTENT_KEYWORD_TIER` | `all` | Keyword layer tier. `all` = hard+soft blocks; `safe_only` / `hard_only` = threat/closing/spam only (soft intents → LLM). |
| `CS_INTENT_DISTILL_PERIOD` | `7d` | T3 distillation cadence. |
| `CS_INTENT_EVAL_PERIOD` | `1d` | T0 eval cadence. |
| `CS_INTENT_PROMOTE_MIN_ACCURACY_DELTA` | `0.0` | Candidate must beat current by this. |
| `CS_INTENT_REBUILD_THRESHOLD` | `-0.02` | Week-over-week drop triggering rebuild mode. |
| `CS_INTENT_KEYWORD_OPTIMIZE_PERIOD` | `1d` | Keyword failure-bank / overlay loop cadence (docs; cron invokes `jobs/optimize_keyword.py`). |
| `CS_INTENT_KEYWORD_OVERLAY_MIN_SUPPORT` | `2` | Min recurring FP phrase count before proposing a fallthrough overlay. |
| `CS_INTENT_KEYWORD_OVERLAY_MAX_RULES` | `12` | Cap new overlay rules proposed per cycle. |
| `CS_INTENT_KEYWORD_PROMOTE_MAX_GOLDEN_DROP` | `0.0` | Max allowed golden accuracy drop when promoting overlays (0 = no drop). |

## HTTP API

| Method | Path | Caller |
|--------|------|--------|
| `GET` | `/health` | Console probe |
| `POST` | `/classify` | cs-ops-bridge seam (inbound) |
| `GET` | `/gate-extract/{session_id}?env=` | cs-ops-bridge seam (brief injection) |
| `GET` | `/intent/{session_id}?env=` | Console (read predicted + corrected) |
| `POST` | `/intents/batch` | Console session list (multi-intent chips, one call per page) |
| `PATCH` | `/intent/{session_id}` | Console (operator correction) |
| `GET` | `/learning/intent-metrics` | Console effect panel |
| `GET` | `/learning/intent-trend` | Console pass-rate chart |
| `GET` | `/learning/distill-log` | Console distill decision log |
| `GET` | `/learning/keyword-optimize-log` | Keyword overlay promote/reject audit |
| `GET` | `/config/intent-scope` | Console workbench (processing scope / close-bar) |
| `GET` | `/config/keyword-tier` | Active `CS_INTENT_KEYWORD_TIER` |

## Processing scope (`config/intent_scope.yaml`)

Controls which intents the AI auto-handles (`in_scope: true`). Out-of-scope intents still appear in classification but are routed to human operators. The Console workbench shows a **关闭工单** bar below the composer when the **primary intent** (AI or operator-corrected) is out of scope.

Override from Console without restarting the classifier:

```bash
export CS_CONSOLE_INTENT_SCOPE=product_inquiry,logistics_inquiry
```

Console loads scope via `GET /api/classifier/intent-scope` (priority: `CS_CONSOLE_INTENT_SCOPE` → classifier → built-in default).

## gate_extract schema (summary)

See `schemas.py` for the canonical pydantic models. Highlights:

- `intents`: list of `IntentItem` (multi-intent, each with its own `in_scope`).
- `in_scope` (top-level) = `any(item.in_scope)` — gate passes if any intent is in AI scope.
- `customer_region`: `{country, province_state, source, confidence}` with source priority `order_address > visitor_geo > email_mention > email_tld > unknown`. The bridge seam fetches country from the Povison order-track API + province_state from QuickCEP `getOrderDetail.billingAddress` (parsed from the `street,city,state,country` string). The order-track API's own `state`/`city` fields are warehouse location (not customer) so they are not used.
- `uncertain_fields` / `null_fields`: explicit no-fabrication labeling.
- `fabrication_guard`: bool self-assertion; HTTP 422 when it can't be asserted.
- `is_conversation_closing`: `true` when the email is a pure thank-you / acknowledgment with no new question (e.g. "Thank you so much for your help!"). Distinct from spam — it's a real customer in an existing thread signaling the conversation is done. When `true`: `in_scope=true`, `route=auto_handle`, `urgency=low`, `emotion=grateful`. The agent brief includes a closing instruction block (send brief acknowledgment, close session). Detected by keyword layer (thank-you patterns + question-marker exclusion + length cap) and LLM layer (prompt rules).

**Payment / BNPL disambiguation:** Afterpay / "after pay" / Klarna / checkout payment declined → `order_management` (pre-purchase checkout), **not** `after_sale_issue`. Keyword layer matches these before after-sale patterns; LLM prompt includes explicit examples in `config/intent_prompt_v1.md`.

## No-fabrication contract (enforced in `config/intent_prompt_v1.md`)

1. Unknown field → null + `null_fields`; low-confidence → `uncertain_fields`.
2. Product slugs / order numbers must appear in the email or metadata — never inferred.
3. `customer_region` only from the source priority; no signal → null.
4. `fabrication_guard=true` is mandatory; failure returns 422, not fake data.
5. Eval golden set includes "should be null" cases — fabricated values fail the eval.

## Keyword tier + guards (schemes 2–3)

- **Hard blocks** (always): legal/threat, conversation-closing thank-you, clear B2B/spam.
- **Soft blocks** (only when `CS_INTENT_KEYWORD_TIER=all`): checkout/BNPL, after-sale, order mgmt, logistics, product.
- **Precision guards** on soft hits: e.g. logistics requires tracking language (not bare order #); product skips when refund/damage/cancel present; spam greeting skips when subject is `Re:` + order/SKU; after-sale skips when checkout/BNPL language is present.
- **Overlays** (`config/keyword_overlays.yaml`): auto-learned fallthrough regexes that force soft hits → LLM when they match.

## Learning loop

- **T1 error bank (automatic)**: operator corrections with `classifier_source=keyword` and wrong primary are synced into `eval/cases/failures.jsonl` (on `PATCH /intent` + `jobs/optimize_keyword.py`). Deduped by correction id + fingerprint. Failure cases use `expected_outcome: keyword_miss` (goal = fall through to LLM).
- **T1b keyword optimize** (`jobs/optimize_keyword.py`, daily): propose fallthrough overlays from recurring FP phrases → **self-eval** on golden + failure bank → promote only if **golden accuracy does not drop** (failures alone cannot outweigh a golden regression) and failure FP rate improves. Rejected candidates are audited, never applied. Writes `config/keyword_overlays.yaml` (+ archive).
- **T2 few-shot** (`jobs/optimize_fewshot.py`, ~6h audit; examples built at classify time): recent corrections injected into the **LLM** prompt with **subject + body** text (not label-only; correction `reason` is unused — Console no longer collects it). Body is quote-stripped (same markers as bridge `intent_gate._strip_quoted_reply`) and falls back to predicted snippets for older rows.
- **T3 distill** (`jobs/optimize_distill.py`, weekly): aggregates corrections (subject/body + predicted→corrected) → LLM generates/edits `config/intent_policy.md` (LLM path). Adaptive:
  - this week ≥ last week pass-rate → `repair` mode (incremental ADJUST:/REMOVE:)
  - this week < last week → `rebuild` mode (regenerate from cumulative samples)
  - Candidate must beat current **golden** eval accuracy to auto-promote (version bump + archive).
  - All decisions audited in `cs_learning_job_runs`; Console surfaces them.

### Self-iteration closed loop (keyword)

```
operator corrects keyword FP
  → sync failures.jsonl (automatic)
  → optimize_keyword proposes overlays
  → eval: golden must not regress; failure FP rate must improve
  → promote overlays OR reject + audit
  → next inbound: soft keyword + overlays + guards → fewer FPs
```

Run manually:

```bash
cd plugins/cs-intent-classifier
CS_INTENT_ENV=LIVE python3 jobs/optimize_keyword.py
# or: python3 -m jobs.optimize_keyword
```

## Tests

```bash
cd plugins/cs-intent-classifier
CS_INTENT_DB_PATH=/tmp/cs_intent_test.db python3 -m pytest tests/ -q
```

## Layout

```
plugins/cs-intent-classifier/
├── manifest.json          # plugin manifest
├── __init__.py            # registration entry
├── serve.py               # standalone FastAPI runner (port 8082)
├── plugin_api.py          # HTTP router
├── schemas.py             # pydantic gate_extract contract
├── db.py                  # self-contained SQLite (4 tables)
├── classifier.py          # keyword pre-filter + LLM fallback
├── intent_provider.py     # thin HTTP client (reference impl for bridge seam)
├── learning.py            # T2 few-shot + T3 distill + promote
├── keyword_learning.py    # T1b failure-bank sync + overlay self-eval
├── eval_runner.py         # golden + failures eval + fabrication detection
├── config/
│   ├── intent_prompt_v1.md    # LLM prompt with no-fabrication contract
│   ├── intent_policy.md       # distilled rules (starts empty)
│   ├── intent_version.txt     # current model_version
│   ├── intent_scope.yaml      # in_scope whitelist
│   ├── intent_learning.yaml   # distill/eval/keyword-optimize cadence
│   ├── keyword_overlays.yaml  # auto-promoted fallthrough overlays
│   └── archive/               # historical policy + overlay versions
├── eval/cases/
│   ├── golden.jsonl           # seed golden set
│   ├── failures.jsonl         # auto-synced keyword FPs
│   └── keyword_sync_state.json
├── jobs/
│   ├── eval_daily.py          # T0 daily eval snapshot
│   ├── optimize_distill.py    # T3 weekly distill + auto-promote
│   ├── optimize_fewshot.py    # T2 high-freq few-shot refresh
│   └── optimize_keyword.py    # T1b keyword overlay loop
├── scripts/cs_intent_cli.py   # ops CLI (classify/get/eval/promote)
└── tests/                     # classifier / correction / learning / eval
```
