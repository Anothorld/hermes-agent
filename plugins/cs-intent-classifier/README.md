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
| `CS_INTENT_DISTILL_PERIOD` | `7d` | T3 distillation cadence. |
| `CS_INTENT_EVAL_PERIOD` | `1d` | T0 eval cadence. |
| `CS_INTENT_PROMOTE_MIN_ACCURACY_DELTA` | `0.0` | Candidate must beat current by this. |
| `CS_INTENT_REBUILD_THRESHOLD` | `-0.02` | Week-over-week drop triggering rebuild mode. |

## HTTP API

| Method | Path | Caller |
|--------|------|--------|
| `GET` | `/health` | Console probe |
| `POST` | `/classify` | cs-ops-bridge seam (inbound) |
| `GET` | `/gate-extract/{session_id}?env=` | cs-ops-bridge seam (brief injection) |
| `GET` | `/intent/{session_id}?env=` | Console (read predicted + corrected) |
| `PATCH` | `/intent/{session_id}` | Console (operator correction) |
| `GET` | `/learning/intent-metrics` | Console effect panel |
| `GET` | `/learning/intent-trend` | Console pass-rate chart |
| `GET` | `/learning/distill-log` | Console distill decision log |

## gate_extract schema (summary)

See `schemas.py` for the canonical pydantic models. Highlights:

- `intents`: list of `IntentItem` (multi-intent, each with its own `in_scope`).
- `in_scope` (top-level) = `any(item.in_scope)` — gate passes if any intent is in AI scope.
- `customer_region`: `{country, province_state, source, confidence}` with source priority `order_address > visitor_geo > email_mention > email_tld > unknown`. The bridge seam fetches country from the Povison order-track API + province_state from QuickCEP `getOrderDetail.billingAddress` (parsed from the `street,city,state,country` string). The order-track API's own `state`/`city` fields are warehouse location (not customer) so they are not used.
- `uncertain_fields` / `null_fields`: explicit no-fabrication labeling.
- `fabrication_guard`: bool self-assertion; HTTP 422 when it can't be asserted.

## No-fabrication contract (enforced in `config/intent_prompt_v1.md`)

1. Unknown field → null + `null_fields`; low-confidence → `uncertain_fields`.
2. Product slugs / order numbers must appear in the email or metadata — never inferred.
3. `customer_region` only from the source priority; no signal → null.
4. `fabrication_guard=true` is mandatory; failure returns 422, not fake data.
5. Eval golden set includes "should be null" cases — fabricated values fail the eval.

## Learning loop

- **T1 error bank**: `eval/cases/golden.jsonl` (seeded) + `failures.jsonl` (auto-appended from corrections).
- **T2 few-shot** (`jobs/optimize_fewshot.py`, ~6h): recent high-confidence corrections injected into the prompt at classify time.
- **T3 distill** (`jobs/optimize_distill.py`, weekly): aggregates corrections → LLM generates/edits `config/intent_policy.md`. Adaptive:
  - this week ≥ last week pass-rate → `repair` mode (incremental ADJUST:/REMOVE:)
  - this week < last week → `rebuild` mode (regenerate from cumulative samples)
  - Candidate must beat current eval accuracy to auto-promote (version bump + archive).
  - All decisions audited in `cs_learning_job_runs`; Console surfaces them.

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
├── eval_runner.py         # golden-set eval + fabrication detection
├── config/
│   ├── intent_prompt_v1.md    # LLM prompt with no-fabrication contract
│   ├── intent_policy.md       # distilled rules (starts empty)
│   ├── intent_version.txt     # current model_version
│   ├── intent_scope.yaml      # in_scope whitelist
│   ├── intent_learning.yaml   # distill/eval cadence config
│   └── archive/               # historical policy versions
├── eval/cases/
│   ├── golden.jsonl           # seed golden set
│   └── failures.jsonl         # auto-appended failures
├── jobs/
│   ├── eval_daily.py          # T0 daily eval snapshot
│   ├── optimize_distill.py    # T3 weekly distill + auto-promote
│   └── optimize_fewshot.py    # T2 high-freq few-shot refresh
├── scripts/cs_intent_cli.py   # ops CLI (classify/get/eval/promote)
└── tests/                     # classifier / correction / learning / eval
```
