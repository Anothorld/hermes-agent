# POVISON SEO Studio — Bridge + Gateway

Lightweight local stack that connects the SEO Studio UI to the skill's
deterministic Python scripts and the `povison-seo` Hermes agent profile.

```
Browser (Studio UI)  ──HTTP──►  Bridge (FastAPI :8766)  ──subprocess──►  skill scripts/
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
./start.sh             # bridge :8766 + gateway :8644, opens browser
# Studio  → http://127.0.0.1:8766/
```

Other modes: `./start.sh bridge` (UI only) · `./start.sh gateway` (agent only) ·
`./start.sh stop` · `./start.sh status`.

## API surface

| Endpoint | Wraps / does |
|----------|--------------|
| `GET /` | Serves the Studio HTML |
| `GET /api/health` | profile, scripts_ok, gateway_key_set, db stats, **Feishu auth flags** |
| `GET /auth/feishu/login` | AnyCross OIDC 登录跳转 |
| `GET /auth/feishu/callback` | OIDC 回调 → session cookie |
| `POST /auth/feishu/h5-token` | 飞书客户端 H5 免登 |
| `GET /auth/me` | 当前登录操作员 |
| `POST /auth/logout` | 清除 session |
| `POST /api/tasks` | Create task (3 steps) or fork (`parent_task_id` + `fork_step`) |
| `POST /api/tasks/import` | Import legacy `run-*` dirs from `SEO_RUNS_DIR` into tasks/steps |
| `POST /api/tasks/reset` | Wipe all tasks; keep the keyword pool by default (`{"drop_keywords":true}` to wipe all) |
| `GET /api/tasks` | List tasks with status (`idle` / `running` / `completed`) |
| `GET /api/tasks/{id}` | Task detail + step metadata |
| `POST /api/tasks/{id}/activate` | Open task in UI — `completed` → `idle` |
| `POST /api/tasks/{id}/audit` | Record an operator action in `audit_log` (e.g. confirm placements) |
| `GET/PUT /api/tasks/{id}/steps/{n}/data` | Read/write step JSON (1=keywords, 2=topics, 3=articleState) |
| `POST /api/tasks/{id}/steps/{n}/run` | Run script for step; ingest into DB |
| `POST /api/tasks/{id}/steps/{n}/agent` | Launch Agent; saves via `seo_save_step_data` |
| `GET /api/tasks/{id}/steps/{n}/agent/status` | Poll gateway run status |
| `GET /api/tasks/{id}/steps/{n}/agent/progress` | Live progress rows (read-only) |
| `GET /api/wordpress/health` | WP connection config + REST/auth status (no secrets) |
| `POST /api/tasks/{id}/wordpress/draft` | Export the task's article to WordPress as a draft (reuses `wordpress_mcp`) |
| `POST /api/runs` | *(legacy)* Create a run directory |
| `GET/PUT /api/runs/{id}/file/{name}` | *(legacy)* Read/write run artifacts |
| `GET /api/jobs/{job_id}` | Poll background script job |
| `POST /api/runs/{id}/keywords/discover` | `keyword-discovery.py` |
| `POST /api/runs/{id}/keywords/enrich` | `enrich-keyword-metrics.py` |
| `POST /api/runs/{id}/topics/brainstorm` | `topic-brainstorm.py` (script fallback) |
| `POST /api/runs/{id}/sections/generate` | `section-generate.py` |
| `POST /api/runs/{id}/validate` | `validate-article.py` |
| `POST /api/runs/{id}/preview` | Assemble `preview.html` |
| `POST /api/runs/{id}/agent` | *(legacy)* Gateway agent |
| `GET /api/runs/{id}/agent/status` | *(legacy)* Poll gateway |
| `GET /api/runs/{id}/agent/progress?since=N` | *(legacy)* JSONL progress |

## Operator UX — job progress

All bridge buttons (发现关键词 / 补指标 / 选题 / 生成正文) share an Apple-style progress card
that appears top-center while a job runs:

- The triggering button is **disabled and shows a spinner** (`is-loading` class) until the job finishes.
- The card shows the job title, a human status hint (e.g. "正在爬取 16 个家居博客站点…"),
  a live elapsed timer, and an indeterminate progress bar.
- On success the card turns green and auto-dismisses (~1.8s); on failure it turns red and
  stays ~4s with the error message.

`topic-brainstorm` **primary path is the Agent** (`POST /agent` with `step=brainstorm`): SERP-driven
topic generation with live progress via tool `seo_report_progress`. The script endpoint remains a
fallback when Gateway is offline. Progress lines land in `{run}/agent-progress.jsonl` and are
polled by the UI (`#bsLive` / `#genLive`).

**Profile plugin:** `~/.hermes/profiles/povison-seo/plugins/seo_studio_progress/` (must be listed
under `plugins.enabled` in the profile `config.yaml`). Restart gateway after enabling.

**Agent run policy:** all Studio-launched agent runs send `"yolo": true` (no manual approval needed).
The profile `config.yaml` sets `platform_toolsets.api_server` to the same toolset list as `cli`
**minus `code_execution`** — so `execute_code` is unavailable to API-server agent runs; the agent
must use the `terminal` tool for shell commands. Changing this requires editing the profile
`config.yaml` and restarting the gateway (`./start.sh restart`).

**Keyword list hydration:** on Bridge connect, the UI auto-loads `kw.json` from the active run
(if the in-memory list is empty). After discover/enrich completes, the list refreshes, filter
resets to「全部来源」, and the view switches to Step 1 so operators see results immediately.
(LLM-cleaned keywords are tagged `搜索趋势` — filtering by brand/media alone may show zero rows.)

**Keyword kinds (Step 1):** keywords are split into two groups:
- **品类关键词 (category, top section)** — manually added; each has an on/off toggle that controls whether it participates in the next brainstorm. A built-in `不限定品类` row always exists (default **off**, not removable); turning it on means "no category anchor". Multiple enabled categories are **merged into one topic set** per brainstorm run.
- **联想关键词 (associative, bottom section, collapsed by default)** — produced by `keyword-discovery.py` / import. Each has a binary **必定包含 / 必定排除** toggle (replaces the old 0–1 weight slider): 必定包含 = in the random pool (default for new/imported); 必定排除 = never used.

**Brainstorm (Step 2) trigger:** requires ≥1 enabled category AND ≥1 associative 必定包含 keyword. The brainstorm builds topics **around the enabled category keywords**, randomly combining 3–8 associative keywords per topic. The Agent path is primary; the script fallback (`topic-brainstorm.py --categories`) mirrors the same logic.

## Env (set by `start.sh`)

| Var | Default | Purpose |
|-----|---------|---------|
| `SEO_SKILL_DIR` | `~/.hermes/skills/productivity/povison-seo-blog` | Scripts + templates + data |
| `SEO_RUNS_DIR` | `$SEO_SKILL_DIR/runs` | Per-run artifact directories |
| `SEO_STUDIO_HTML` | `ui/index.html` | UI to serve at `/` |
| `HERMES_GATEWAY_BASE` | `http://127.0.0.1:8644` | povison-seo gateway |
| `HERMES_GATEWAY_KEY` | profile `API_SERVER_KEY` | Gateway bearer token |
| `SEO_SCRIPT_TIMEOUT` | `600` | Per-script subprocess timeout (s) |
| `SEO_LLM_BASE_URL` | `https://ai-endpoint.povison-inc.com/v1` | OpenAI-compatible LLM endpoint (discover / brainstorm / sections) |
| `SEO_LLM_API_KEY` | *(required in `.env`)* | Bearer token for the LLM endpoint |
| `SEO_LLM_MODEL` | `glm-5.2` | Model id (`glm-2` is not available on this endpoint; use `glm-5.2`) |

Copy `.env.example` → `.env` and set `SEO_LLM_API_KEY` before running brainstorm or section generation.

**Feishu 应用登录**（参考 povison-cs-console）：复制 `.env.example` 中的 `SEO_STUDIO_*` 块，配置 H5 免登和/或 AnyCross OIDC。详见 [docs/seo-studio-feishu.md](../../../docs/seo-studio-feishu.md)。

| Var | Default | Purpose |
|-----|---------|---------|
| `SEO_STUDIO_FEISHU_APP_ID` | — | 飞书自建应用 App ID（客户端内免登） |
| `SEO_STUDIO_FEISHU_APP_SECRET` | — | 飞书自建应用 Secret |
| `SEO_STUDIO_OIDC_*` | — | AnyCross OIDC 四元组（浏览器登录） |
| `SEO_STUDIO_SESSION_SECRET` | — | Session 签名密钥（启用登录时必填） |
| `SEO_STUDIO_REQUIRE_LOGIN` | `auto` | `auto` / `1` / `0` |
| `SEO_STUDIO_COOKIE_SECURE` | `1` | 内网 HTTP 设为 `0` |

## Deterministic vs open-ended

- **Scripts** (deterministic CRUD): keyword discovery, metrics enrich, topic
  brainstorm, section generate, validate, preview. The bridge runs them via
  `subprocess` and returns a `job_id` for polling.
- **Agent** (open-ended): 「开始生成」头脑风暴 + 「请 Agent」按钮 POST 到 povison-seo gateway
  with `step` (`brainstorm` / `serp` / `outline` / `section` / …) and skill `povison-seo-blog`.
  Agent reports live progress via `seo_report_progress`. Reserve for steps that need
  tools/skills/MCP — not for deterministic IO.
- **Agent routing:** brainstorm uses `POST /api/tasks/{id}/steps/2/agent`; all Step 3
  sub-steps (serp / outline / section / faq / meta / placements) use
  `POST /api/tasks/{id}/steps/3/agent` with `step=<sub-step>`. The agent persists the
  full `articleState` via `seo_save_step_data(task_id, step_num=3, …)` after each sub-step
  so the UI can render it from the DB. Legacy `run-*` ids fall back to
  `POST /api/runs/{id}/agent` (disk-file based).
- **DB vs file writes:** for `task-*` ids the SQLite DB is the source of truth. File PUTs
  (`PUT /api/runs/{id}/file/article-state.json`) only write the shadow copy on disk and do
  **not** overwrite the DB step data — this prevents a stale in-memory `articleState` (e.g.
  missing an outline the agent already saved) from clobbering the authoritative DB state.
  UI edits persist via `PUT /api/tasks/{id}/steps/3/data` (`persistArticleState`); agent
  results persist via `seo_save_step_data`.
- **Deterministic script endpoints hydrate from DB:** the legacy script wrappers
  (`POST /api/runs/{id}/topics/brainstorm`, `.../sections/generate`, `.../validate`) read/write
  run-dir files. For `task-*` ids they **hydrate the input file from the DB before running the
  script**, so the script reads authoritative data (not the stale in-memory copy the UI just
  saved via `bridgeSaveFile`). The completion callback then writes the script result back to the
  DB — safe because the result is a superset of the authoritative DB state. Without hydration
  these callbacks would clobber agent-saved fields (e.g. outline/serp) with a state derived from
  stale input. The DB-native `POST /api/tasks/{id}/steps/{n}/run` endpoints hydrate the same way.
- **SERP schema:** `articleState.serp = {ranks:[{cluster, results:[{title,url,domain,angle}]}], gaps:[string]}`.
  The UI renders each cluster with its top results; gaps render as chips. This SERP
  output is the reference input for the next sub-step (outline generation).
- **Image policy (Step 3):** the agent inserts two kinds of images, from strictly separate sources so they never conflict:
  - **Body images** — `articleState.sections[].images = [{url, alt, caption, credit}]`. Source MUST be
    Unsplash or Pexels (license-free). The agent searches via web/browser for a direct image URL that
    matches the section topic; skips a section if no good match (never forces a wrong image). Never
    duplicates an image across sections.
  - **Product images** — `articleState.products[].image = <POVISON product image URL>`. Source MUST be
    POVISON (povison.com), fetched via the `povison_product` tool or by browsing the product page. If a
    product image cannot be found, `image` is left empty (never substituted with a stock photo).
  - The UI renders body images as `<figure>` after each section's text and product images as a distinct
    `product-figure` block. The preview template styles both (`.article-figure` / `.article-figure.product-figure`).
  - The blog template **no longer** includes the legacy footer gallery (`Visual Inspiration` / 10 stock
    Pexels tiles). Preview and WordPress export end after the article body (+ hero in the full preview shell).

- **WordPress draft export (final step):** the "审核通过，导出到 WordPress 草稿" button publishes the
  finalized article to WordPress as a draft in one click. It reuses the operator's existing
  `wordpress_mcp` package (`/Users/arnold/mcp-servers/wordpress-mcp/`) — the same pipeline the agent
  uses via MCP — so SEO Studio and the agent share one publish path.
  - **Flow:** `POST /api/tasks/{id}/wordpress/draft` → loads step-3 `articleState` from DB →
    `fill_blog_template(state)` builds the full template HTML → `wp_publish.publish_draft()` calls
    `wordpress_mcp.publisher.create_draft_from_html`, which parses `<article class="article-body">`
    as post content and extracts title / slug / meta-description / FAQ-schema from the head, writes
    Rank Math SEO meta, and creates the post (`status=draft`). Returns `post_id` + `edit_url` +
    `preview_url`; the UI opens the WP edit screen in a new tab.
  - **Credentials:** read from the profile `config.yaml` `mcp_servers.wordpress.env` block
    (`WP_BASE` / `WP_USER` / `WP_APP_PASS` / `WP_CATEGORY_ID` / `WP_TAG_IDS` / `SEO_PLUGIN`) — the single
    source of truth. Environment variables override config.yaml when set. The Application Password is
    never hardcoded; `wp_publish.py` only reads it at runtime.
  - **Images:** referenced by their original URL (no media-library sideload) for the MVP — fast and
    avoids downloading remote assets on the operator's click. Pass `{"skip_image_upload": false}` in
    the request body to sideload each image into the WP Media Library (and set the first as featured).
  - **Category/tags:** defaults come from `WP_CATEGORY_ID` / `WP_TAG_IDS`; override per-export by passing
    `category_id` (int) and `tag_ids` (list[int]) in the request body.
  - **Health:** `GET /api/wordpress/health` reports config presence + REST/auth status (no secrets) for
    pre-flight checks.
  - **Deps:** `beautifulsoup4`, `requests`, `pyyaml` added to `requirements.txt` (parser + client + config
    reader). The `wordpress_mcp` src is auto-discovered from config.yaml `PYTHONPATH` or
    `~/mcp-servers/wordpress-mcp/src` (override with `WORDPRESS_MCP_SRC`).

## Database (source of truth)

`db.py` — SQLite (`seo_studio.db`) is the **primary** store for tasks and step payloads.

| Concept | Meaning |
|---------|---------|
| **task** | One pipeline instance (`task-{timestamp}-{nonce}`) with status `idle` / `running` / `completed` |
| **step 1** | Keywords JSON array (shared pool — carried into new tasks) |
| **step 2** | Topics envelope |
| **step 3** | Full `articleState` blob |
| **branch** | `POST /api/tasks` with `parent_task_id` + `fork_step` copies steps 1..N |

**Status rules:**
- `running` — a script/agent job is in flight (shown in 运行审计)
- `completed` — step 3 saved as done
- Opening a `completed` task (`POST …/activate`) flips it to `idle`

**Keyword pool:** keywords are a shared pool across tasks. Creating a new task from scratch keeps the current keywords and writes them into the new task's step 1; forking copies steps 1..N from the parent. Unfinished steps (current + later) always show empty in the UI when a task is opened — `restoreWorkbenchUI` resets every Step 3 phase panel (SERP / outline / sections / placements / FAQ / meta) before populating from the loaded task, so a parent's rendered output never bleeds into a fork.

**Auto-fork on selection change:** when an in-progress step already has output and the operator changes that step's selection before clicking the next-step button, the action opens a branch instead of overwriting:
- **Brainstorm (开始头脑风暴):** if topics already exist and the selected keywords differ from the ones that produced them (step 2 `input_keywords`), clicking 开始头脑风暴 forks from step 2 — the old topics are preserved, the new branch re-runs the brainstorm with the new keywords.
- **Generation (生成博客):** if the task already has an article for topic A and the operator picks a different topic B, clicking 生成博客 forks from step 2 — the old article is preserved, the new branch starts a fresh article for topic B.
- Unchanged selection + click proceeds in place (no fork).

**Concurrent flows while an Agent runs:** the operator can start a new task (新建运行) while an Agent is running on another task — the previous task keeps running in the background. Switching back to a `running` task resumes the progress bar and live panel (`resumeTaskAgentPolling`) so the operator sees the ongoing Agent progress. Branching is automatic (see above) — there are no manual fork buttons.

Scripts still use a shadow directory under `SEO_RUNS_DIR/{task_id}/` for `-o` paths; results are ingested into `steps.data_json`. Agent writes via tools `seo_save_step_data` / `seo_report_progress` (profile plugin).

Legacy tables (if present) are renamed to `_legacy_*` on first init.

### Import legacy runs

Disk run folders under `SEO_RUNS_DIR` are **not** auto-imported. After upgrading to the task model, run once:

```bash
cd hermes-agent/playground/seo-studio
python3 scripts/import-legacy-runs.py
# or: POST /api/tasks/import  (imports all run-* dirs)
# or: POST /api/tasks/import {"run_id":"run-20260714-182800-099532"}
```

Imported tasks keep the original `run-*` id so script shadow paths stay aligned. Re-import with `--force` or `{"force": true}` to overwrite.

## Sync with standalone UI

`ui/index.html` is the source of truth (has the bridge layer). The standalone
copy lives at `~/povison-seo-studio.html` for offline editing. Keep them in
sync:

```bash
cp ui/index.html ~/povison-seo-studio.html   # or vice-versa after UI edits
```

## Rule blocks (生成规则)

Operator-editable writing rules live in the Studio **规则设置** panel and are
injected into agent prompts at generation time. Defaults are defined in
`DEFAULT_RULES` (`ui/index.html`) and surfaced via the `RULE_BLOCKS` registry.

| Block id | Injected when | Purpose |
|----------|---------------|---------|
| `global` | every generation | 语气、语言、品牌底线、事实核查底线 |
| `outline` | `mode=outline` | H2–H3 大纲结构与 SERP 覆盖 |
| `intro` | section `type=Intro` | Introduction 写作约束 |
| `h2` | section `type=H2` | 各 H2 正文写作约束（过渡段、数据、体验感等） |
| `conclusion` | section `type=Conclusion` | Conclusion 写作约束 |
| `placements` | `mode=placements` | 产品植入与内链规则 |
| `faq` | `mode=faq` | FAQ 写作约束 |
| `meta` | `mode=meta` | Meta Title / Description / Slug 规则 |
| `listicle` | outline + h2 + placements（追加） | 榜单类（Top/Best）专属结构；规则自带"榜单类"前提，非榜单主题时自然失效 |
| `images` | global + meta（追加） | 配图与产品图规则 |

### Versioning

Rules persisted to `localStorage` carry a `version` field (current
`DEFAULT_RULES_VERSION = '2.0'`). When a stored envelope's version differs from
the current default, `applyRulesEnvelope` rebuilds from `DEFAULT_RULES` while
preserving the operator's enabled/disabled toggles on matching rule texts and
appending any user-added custom rules. This lets default rule updates roll out
to existing users without wiping their customizations.

