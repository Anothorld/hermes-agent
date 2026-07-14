# POVISON SEO Studio — Bridge + Gateway

Lightweight local stack that connects the SEO Studio UI to the skill's
deterministic Python scripts and the `povison-seo` Hermes agent profile.

```
Browser (Studio UI)  ──HTTP──►  Bridge (FastAPI :8765)  ──subprocess──►  skill scripts/
                                      └──httpx──►  Hermes Gateway (:8644, profile=povison-seo)
```

## Layout

| Path | Role |
|------|------|
| `start.sh` | Starts bridge + gateway, manages ports/keys, opens browser |
| `server.py` | Single-file FastAPI bridge: serves UI, wraps scripts, delegates to gateway |
| `ui/index.html` | Studio UI (also mirrored at `~/povison-seo-studio.html`) |
| `requirements.txt` | `fastapi`, `uvicorn`, `httpx` |
| `.venv/` | Local venv (created by `./start.sh install`) |

## Quick start

```bash
cd hermes-agent/playground/seo-studio
./start.sh install     # create venv + pip install (first time)
./start.sh             # bridge :8765 + gateway :8644, opens browser
# Studio  → http://127.0.0.1:8765/
```

Other modes: `./start.sh bridge` (UI only) · `./start.sh gateway` (agent only) ·
`./start.sh stop` · `./start.sh status`.

## API surface

| Endpoint | Wraps / does |
|----------|--------------|
| `GET /` | Serves the Studio HTML |
| `GET /api/health` | profile, scripts_ok, gateway_key_set |
| `POST /api/runs` | Create a run directory under `SEO_RUNS_DIR` |
| `GET/PUT /api/runs/{id}/file/{name}` | Read/write run artifacts (kw.json, topics.json, article-state.json, …) |
| `GET /api/jobs/{job_id}` | Poll background script job |
| `POST /api/runs/{id}/keywords/discover` | `keyword-discovery.py` |
| `POST /api/runs/{id}/keywords/enrich` | `enrich-keyword-metrics.py` (`seed_demo`) |
| `POST /api/runs/{id}/topics/brainstorm` | `topic-brainstorm.py` |
| `POST /api/runs/{id}/sections/generate` | `section-generate.py` (injects `generation-rules.json`) |
| `POST /api/runs/{id}/validate` | `validate-article.py` → R3/R4 checklist |
| `POST /api/runs/{id}/preview` | Assemble `preview.html` from `templates/blog-post-template.html` |
| `POST /api/runs/{id}/agent` | `POST /v1/runs` on povison-seo gateway (open-ended SEO work) |
| `GET /api/runs/{id}/agent/status` | Poll gateway run status |

## Env (set by `start.sh`)

| Var | Default | Purpose |
|-----|---------|---------|
| `SEO_SKILL_DIR` | `~/.hermes/skills/productivity/povison-seo-blog` | Scripts + templates + data |
| `SEO_RUNS_DIR` | `$SEO_SKILL_DIR/runs` | Per-run artifact directories |
| `SEO_STUDIO_HTML` | `ui/index.html` | UI to serve at `/` |
| `HERMES_GATEWAY_BASE` | `http://127.0.0.1:8644` | povison-seo gateway |
| `HERMES_GATEWAY_KEY` | profile `API_SERVER_KEY` | Gateway bearer token |
| `SEO_SCRIPT_TIMEOUT` | `600` | Per-script subprocess timeout (s) |

## Deterministic vs open-ended

- **Scripts** (deterministic CRUD): keyword discovery, metrics enrich, topic
  brainstorm, section generate, validate, preview. The bridge runs them via
  `subprocess` and returns a `job_id` for polling.
- **Agent** (open-ended): "请 Agent" buttons POST to the povison-seo gateway
  with `instructions` naming skill `povison-seo-blog`. Use for SERP analysis,
  WordPress publish, multi-step reasoning. Reserve for steps that need
  tools/skills/MCP — not for deterministic IO.

## Database (workflow record)

`db.py` — a SQLite layer (`seo_studio.db` next to `server.py`) records every
workflow event for queryable history. **Files remain the handoff source of
truth**; the DB is the index/audit layer.

| Table | Records |
|-------|---------|
| `runs` | one row per run dir (id, label, status, created_at) |
| `keywords` | keyword rows post-enrich (sv/kd/cpc/intent) |
| `topics` | brainstormed topics (priority_score, serp_gap) |
| `article_state` | latest article snapshot (phase, word_count, validation) |
| `audit_log` | every script job + file save + agent call + gate |
| `agent_runs` | gateway run delegations |
| `generation_rules` | per-run rule snapshot (block, text, enabled) |

Query endpoints:

| Endpoint | Returns |
|----------|---------|
| `GET /api/history` | recent runs + DB stats |
| `GET /api/runs/{id}/detail` | full run record (keywords, topics, audit, agent) |
| `GET /api/db/stats` | table row counts |

The DB auto-initializes on bridge startup; no manual setup needed.

## Sync with standalone UI

`ui/index.html` is the source of truth (has the bridge layer). The standalone
copy lives at `~/povison-seo-studio.html` for offline editing. Keep them in
sync:

```bash
cp ui/index.html ~/povison-seo-studio.html   # or vice-versa after UI edits
```
