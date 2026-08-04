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
| `GET /api/stock-images/health` | Pixabay key + Openverse availability (no secrets) |
| `POST /api/stock-images/search` | Body `{query, source?: auto\|pixabay\|openverse, per_page?}` → candidate pool for section images |
| `GET /api/povison-products/health` | POVISON catalog Search API reachability (no secrets; storeId=3) |
| `POST /api/povison-products/search` | Body `{keyword, limit?}` → candidates with image + tags |
| `POST /api/povison-products/lookup` | Body `{url, sku?, variant?}` → name, url, image, specs, dimensions |
| `POST /api/povison-products/recommend` | Body `{topic: {primary_keyword, secondary_keywords, category_keywords}, sections: [...], limit?}` → `products[]` ready for `articleState.products` (with image + fit_score) |
| `POST /api/povison-products/scrape` | Body `{url}` → fallback PDP scrape (JSON-LD Product) |
| `POST /api/povison-products/enrich-image` | Body `{url}` → `{ok, image, name?}` (Detail API first, scrape fallback) |
| `POST /api/povison-products/enrich-batch` | Body `{products: [{url, name?}]}` → updated products with `image` filled where missing |
| `GET /api/povison-blog/health` | POVISON blog sitemap reachability + cached article count (no secrets) |
| `POST /api/povison-blog/search` | Body `{keyword, limit?}` → ranked blog articles `{url, slug, title_guess, category, score, reasons}` |
| `POST /api/povison-blog/recommend-links` | Body `{topic: {primary_keyword, secondary_keywords, category_keywords}, sections, existing_urls?, limit?}` → `links[]` ready for `articleState.links` (all URLs real povison.com/blog/ articles from the sitemap) |
| `POST /api/povison-blog/verify` | Body `{url}` → `{ok, verified, url, article?}` — is this a real povison.com/blog/ article? |
| `GET /api/povison-reviews/health` | magento2 review DB configured? (no secrets surfaced) |
| `GET /api/povison-reviews/by-spu` | Query `?spu=<id>&limit=5&min_rating=4` → APPROVED reviews `{ok, spu, count, reviews[]}` (best-rated first; ratings converted from 0-100 to 1-5 stars). Returns `ok=false` (not 500) when DB not configured |
| `GET /api/povison-reviews/by-url` | Query `?url=<povison product URL>&limit=1&min_rating=4` → resolves the magento numeric SPU from the URL slug (via `catalog_product_entity_varchar.url_key`) then returns reviews `{ok, url, spu, count, reviews[]}`. **This is the endpoint the Editorial Picks Agent uses** — the product card only carries the storefront URL, not the numeric SPU |
| `GET /api/povison-reviews/summary` | Query `?spu=<id>` → aggregate `{ok, spu, reviewsCount, ratingSummary, rating}` from `review_entity_summary` |
| `GET /api/povison-placements/health` | Placement-guard reachability (probes one povison URL) |
| `POST /api/povison-placements/verify-urls` | Body `{urls: ["..."], workers?}` → parallel HTTP liveness check (HEAD→GET, 10s timeout, povison host whitelist). Returns `{ok, checked_at, total, dead_count, results: [{url, live, status_code, final_url, error}]}` |
| `POST /api/tasks/{task_id}/steps/3/verify-placements` | Verify all product + link URLs in the task's step-3 articleState are live; writes `articleState.placementUrlCheck` and forces `placementsConfirmed=false` if any dead |
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

**Keyword pool is global & survives reloads:** category + associative keywords and their toggle states are shared across all tasks. On a fresh page load the in-memory pool is empty, so `bridgeEnsureRun` adopts the task with the richest pool — `hasPool` counts associative keywords, topics, **and** user-added category keywords (excluding the auto `不限定品类`), and among candidates it prefers the highest `kw_count`. A `recoverCategoryKeywords` safety net then scans other tasks' step-1 data for any user-added categories the chosen task lacks and merges them in, so manually-added categories never vanish across sessions.

**Brainstorm (Step 2) trigger:** requires ≥1 enabled category AND ≥1 associative 必定包含 keyword. The brainstorm builds topics **around the enabled category keywords**, randomly combining 3–8 associative keywords per topic. The Agent path is primary; the script fallback (`topic-brainstorm.py --categories`) mirrors the same logic.

**Selected associative chip ops (Step 2):** each associative keyword chip supports **移除** (drop from this brainstorm only), **删除** (permanently remove from the keyword pool / Step 1 DB), and **锁定** (keep on 「换一批」; only unlocked associative keywords are reshuffled). Locked set is stored as `locked_keywords` on the step-2 envelope.

## Env (set by `start.sh`)

The Bridge also auto-loads `.env` at startup (``_load_dotenv`` in `server.py`), so it
works even when started manually (e.g. `uvicorn server:app` without `start.sh`).
Vars already in the real environment take precedence over `.env` values. Both
`playground/seo-studio/.env` and the profile `~/.hermes/profiles/povison-seo/.env`
are loaded (in that order). This is critical for ``SEO_LLM_API_KEY`` — without it,
script-path LLM calls (``section-generate.py --mode meta/faq``) silently fail and
fall back to demo content.

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

**Meta / FAQ JSON mode:** `section-generate.py` requests ``response_format={"type":"json_object"}``
for meta/faq/outline/placements (verified supported on ``glm-5.2``). If a quota failover
lands on a model that rejects ``response_format`` (e.g. ``glm-5.2-b``), ``llm_client``
retries once without it. Reasoning models also need enough ``max_tokens`` so ``content``
is not empty after ``reasoning_content`` (meta uses 8000). If the primary meta call still
cannot be parsed, stderr shows ``[meta] primary LLM response not JSON, using demo draft``
and falls back to ``demo_meta``.

**Merged section + placement flow:** `section-generate.py --mode section` first calls the
Bridge HTTP API (`/api/povison-products/recommend` + `/api/povison-blog/recommend-links`,
resolved via `SEO_STUDIO_BASE_URL` then `SEO_BRIDGE_PORT` then 8766) to fetch a candidate
pool of REAL povison URLs, injects a compact `candidate_pool` into the section prompt, and
the LLM weaves inline markdown links into the prose as it writes (output stays `{id,content}` —
no `placements_used` field, to avoid reasoning-token exhaustion). `_resolve_and_backfill` then
parses the markdown links out of `content`, validates them against the pool, and backfills
`state[products]/[links]` per-section. If the Bridge is unreachable, sections are written
without inline links and the legacy assembly path applies; the static `placement-catalog.json`
is never used as a URL source (known 404s). A server-side step-3 post-save hook
(`_post_save_step3_inline_placements`) re-derives `products`/`links` from `sections[].content`
on every save, covering both the Agent and script paths.

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
  - **Body images** — `articleState.sections[].images = [{url, alt, caption, credit}]` plus
    `section.image_queries = [2–3 concrete English phrases]` (P0). Source MUST be
    **Pixabay or Openverse** via the **candidate pool** (P1):
    `python3 scripts/search-stock-images.py -q "..." -n 5` or
    `POST /api/stock-images/search` (`source`: `auto` | `pixabay` | `openverse`).
    The agent may only pick from returned candidates — never invent URLs / browser-scrape.
    Hard rules: photo must show furniture / room / layout; forbid moving-box close-ups,
    handshakes, abstract textures, unrelated outdoors, portraits with no furniture.
    Skip (`images=[]`) if no good match. Never duplicate URLs.
  - **API keys:** Openverse works anonymously (no key). Optional `PIXABAY_API_KEY` in `.env`
    (see `.env.example`); optional `OPENVERSE_ACCESS_TOKEN` for higher Openverse rate limits.
    Health: `GET /api/stock-images/health`.
  - **Product images** — `articleState.products[].image = <POVISON product image URL>` and
    `products[].url = <PDP URL>`. Source MUST be POVISON (povison.com). The preferred path is the
    POVISON catalog API (keyword search → Detail API takes the main image); PDP scraping is a
    fallback only when the Detail API fails. If a product image cannot be found, `image` is left
    empty (never substituted with a stock photo).
  - **UI buttons (placement sub-zone within the 正文与植入 panel):**
    - **重新挑选植入候选** — launches the Hermes Agent via `bridgeAskAgent('placements')` →
      `POST /api/tasks/{id}/steps/3/agent` with `step=placements` (NOT the demo script path
      `section-generate.py --mode placements`). Bridge injects `_PLACEMENTS_SUBSTEP_GUIDANCE`:
      **inline** mode re-resolves cards from prose / calls recommend APIs for real POVISON URLs;
      **editorial** mode RE-PICKs exactly 3 review cards (catalog + Detail + reviews APIs) and
      fills `editorialTitle`/`editorialIntro` only when empty. Never falls through to
      `demo_placements` / `placement-catalog.json` seed data. Use after editing prose, deleting
      cards, or when the operator wants a fresh pick.
    - **补全缺图** — calls `POST /api/povison-products/enrich-batch` for products that have a
      `url` but no `image`; fills `image` from the Detail API (scrape fallback).
    - **校验链接有效性** — calls `POST /api/tasks/{id}/steps/3/verify-placements` which does a
      parallel HTTP liveness check (HEAD→GET, 10s timeout, povison host whitelist) on every
      product + link URL. Dead URLs (4xx/5xx/unreachable) are written to
      `articleState.placementUrlCheck` and the **确认，写 FAQ** button stays disabled until
      they're fixed and re-verified. This is the second guard rail — it catches real-shaped but
      dead URLs (retired products, unpublished articles) that the pattern validator misses.
  - **URL validation (root-cause fix for 404 placements):** three guard rails.
    1. **Pattern check** (on every save of step-3 data): `products[].url` must be a povison PDP
       (`.html` + povison host); `links[].url` must be `https://www.povison.com/blog/<...>.html`
       or a `/collections/<slug>` landing page. Fabricated URLs →
       `articleState.placementWarnings` + confirm disabled.
    2. **Inline-link scan** (merged flow, on every save of step-3 data): `_post_save_step3_inline_placements`
       scans `sections[].content` for markdown `[text](povison url)` links, validates each URL
       shape, backfills `products`/`links` from the prose when those arrays are empty, and flags
       invented/malformed povison URLs embedded inline (the top-array pattern check cannot see
       these). Covers both the Agent path and the script path.
    3. **Liveness check** (operator-triggered via 「校验链接有效性」): parallel HTTP probe of
       every URL; dead URLs → `articleState.placementUrlCheck` + confirm disabled.
    All render in a red banner listing each offending URL with its HTTP status.
  - Export renders each product as a centered `<figure>`: **image and caption both link to the
    PDP**; caption text is the product name (e.g. `Povison Ansel-…`), matching
    [published blog figures](https://www.povison.com/blog/buying-guide/low-profile-tv-stand.html).
    WordPress draft export also pre-enriches any product still missing an image before publishing.
  - **Placement injection (assembly-time, single source of truth):** two paths.
    - **Merged flow (new tasks):** body sections already contain inline markdown links to REAL
      povison URLs (the section LLM wove them in from the candidate pool). `_prepare_section_content`
      detects inline povison links in `sec.content` and only strips REJECTED placements
      (`_strip_rejected_links_from_prose`: unwraps rejected internal links to plain text; drops
      rejected product blurb paragraphs when blurb-sized ≤100 words). No `Related:` fallback, no
      trailing blurb append — the prose already contains everything.
    - **Legacy flow (old tasks):** section content has no inline povison links; placements live in
      `articleState.products`/`links`. `_strip_legacy_placements` strips trailing
      `[Product: …]` / `[Internal link: …]` blocks, then `_prepare_section_content` weaves accepted
      placements back in:
    - **Internal links** are inlined into the section's first plain-text occurrence of their anchor
      (case-insensitive, word-bounded, preserves the body's original casing) so they read like
      editor-placed inline links rather than trailing footnotes. Links whose anchor does not appear
      in the body fall back to a trailing `Related: [anchor](url)` line so no accepted link is silently
      dropped.
    - **Products** are appended as blurbs at the end with the product name hyperlinked to the PDP.
    Orphan blurbs left by the old buggy write-to-body button (stacked paragraphs with no `[Product:]`
    marker) are also stripped before inject, so re-assembling historical polluted tasks self-heals.
    Re-assembling never accumulates duplicates; `articleState.products`/`links` is the only source.
    Local UI 「组装预览」uses the same `prepareSectionContent` path client-side (not only the Bridge).
  - The UI renders body images as centered `<figure>` after each section's text. **Image placement rule
    (one image per spot):** when a section has an accepted product figure, the general stock illustration
    is omitted so the two don't stack and separate the product blurb from its image — the product image
    wins. Sections without a product image still render their stock illustration as before.
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
  - **Rank Math SEO meta injection (required on this site):** the povison.com WP REST API does
    **not** register `rank_math_*` meta with `show_in_rest=true`, so the standard REST `meta` field
    (used by `create_draft_from_html`) and XML-RPC `custom_fields` both **silently drop** SEO
    title/description/focus-keyword/schema — the Rank Math sidebar would stay empty and plugin
    detection would find nothing. `wp_publish.py` therefore re-injects them after draft creation
    via Rank Math's own REST endpoint `POST /wp-json/rankmath/v1/updateMeta` (the same route the
    block editor calls on save), using Application-Password Basic Auth:
    `_inject_rank_math_seo_meta` writes `rank_math_title` / `rank_math_description` /
    `rank_math_focus_keyword` (focus keyword sourced from `articleState.meta.focus`, falling back
    to the title-derived value); `_inject_rank_math_faq_schema` writes
    `rank_math_schema_FAQPage` (clean FAQPage object). Both are best-effort and report back via
    `result["seo_meta_injected"]` / `result["faq_schema_injected"]`. Only the `rankmath` plugin
    path is handled; `yoast` falls through to the standard (possibly empty) REST meta write.
  - **Credentials:** read from the profile `config.yaml` `mcp_servers.wordpress.env` block
    (`WP_BASE` / `WP_USER` / `WP_APP_PASS` / `WP_CATEGORY_ID` / `WP_TAG_IDS` / `SEO_PLUGIN`) — the single
    source of truth. Environment variables override config.yaml when set. The Application Password is
    never hardcoded; `wp_publish.py` only reads it at runtime.
  - **Images:** by default images are downloaded and sideloaded into the WP Media Library so
    the first image becomes the **featured image** (post cover). Pass `{"skip_image_upload": true}`
    in the request body to keep original URLs instead (no featured image will be set). All
    `<figure>`/`<img>`/`<figcaption>` tags use **`wp-block-image aligncenter`** (WordPress-native)
    plus inline centering styles as fallback. Already-generated articles do **not** need full
    regeneration — restart Bridge, then re-export the WordPress draft (or re-assemble preview).
  - **Markdown tables:** section content may contain GFM pipe tables (`| col | col |`). The export
    path converts them to real HTML `<table>` (via the `markdown` package, with a pipe-table
    fallback). Without this, WordPress would show raw `|` text.
  - **Table of Contents:** a TOC block (Rank Math–style `h2` title + body-sized underlined
    links) is auto-generated from Introduction, body H2s, Conclusion, and nested Q&A
    questions (≥2 entries required) and inserted after the intro. Each heading gets an `id`
    anchor. Styled to match published posts such as the
    [low-profile TV stand guide](https://www.povison.com/blog/buying-guide/low-profile-tv-stand.html).
  - **FAQ / Q&A:** heading is **`Q&A`** (`id="q-a"`). Questions are `h3` (~1.25em) and answers
    are body-sized paragraphs — not the older small gray accordion look. Plain HTML only
    (no forged Rank Math Gutenberg FAQ block). Schema via `rank_math_schema_FAQPage` post meta,
    written through Rank Math's `/wp-json/rankmath/v1/updateMeta` endpoint (see "Rank Math SEO
    meta injection" above) — the old `rank_math_schema` REST-meta write was silently dropped.
  - **Category/tags:** defaults come from `WP_CATEGORY_ID` / `WP_TAG_IDS`. Current profile default is
    **Tips** (`WP_CATEGORY_ID=62`, slug `tips`). Override per-export by passing `category_id` (int)
    and `tag_ids` (list[int]) in the request body. Code fallback (when env/config omit the key) is
    also `62`.
  - **External link `rel=nofollow`:** at assembly time `_add_external_link_rel` walks every `<a href>`
    in the article body and, for **external** links (any `http(s)://` host that is not `povison.com`
    or a subdomain of it), bakes in `target="_blank" rel="noreferrer noopener nofollow"`. Internal
    povison.com links (product PDPs, `/collections/<slug>` pages, `/blog/*.html` articles injected by
    placements) and in-page anchors (`#id`) are left untouched so internal SEO juice is not diluted.
    WordPress preserves `rel` on `<a>` tags received via the REST API, so the draft ships with the
    same link relationships an operator would otherwise set by hand in the block editor.
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

**Keyword pool:** keywords are a shared pool across tasks. Creating a new task from scratch keeps the current keywords and writes them into the new task's step 1; forking copies steps 1..N from the parent. Unfinished steps (current + later) always show empty in the UI when a task is opened — `restoreWorkbenchUI` resets every Step 3 phase panel (SERP / outline / 正文与植入 [sections + placement sub-zone] / FAQ / meta) before populating from the loaded task, so a parent's rendered output never bleeds into a fork.

**Step 3 phase flow (6 pills):** SERP → 大纲 → **正文与植入** (sections editor + product/internal-link sub-zone on one page) → FAQ → SEO 元信息 → 预览发布. Placements are no longer a separate phase pill/panel — they live as a `#placementZone` sub-region inside the 正文与植入 panel. `phaseDone.placements` is retained as a persisted field for backward compatibility but the FAQ gate now reads `phaseDone.sections && (placementsConfirmed || placementStyle==="editorial")`; `recomputeAllGates()` re-locks every gate button from `articleState` on task switch / agent-resume / reject so no stale "enabled" button survives.

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

### POVISON catalog CLI (agent tool)

`scripts/povison-catalog.py` exposes the catalog API to the agent without needing the Bridge HTTP server up:

```bash
# keyword search → candidates (name, url, image, tags)
python3 scripts/povison-catalog.py search -q "low profile tv stand" -n 8

# PDP URL → detail (Detail API): name, url, image, specs, dimensions
python3 scripts/povison-catalog.py lookup --url "https://www.povison.com/..."

# PDP URL → JSON-LD Product fallback (when Detail API fails)
python3 scripts/povison-catalog.py scrape --url "https://www.povison.com/..."

# PDP URL → best-effort {image, name} (Detail API then scrape)
python3 scripts/povison-catalog.py enrich --url "https://www.povison.com/..."

# topic + sections → scored products[] ready for articleState.products
python3 scripts/povison-catalog.py recommend \
  --topic '{"primary_keyword":"low profile tv stand","secondary_keywords":["modern tv stand"]}' \
  --sections /tmp/sections.json --limit 2
```

Set `SEO_STUDIO_DIR` if running from elsewhere. The same operations are available over HTTP via `/api/povison-products/*` (see API table above).

### POVISON blog internal-link CLI (agent tool)

`scripts/povison-blog.py` exposes the blog-sitemap search to the agent without needing the Bridge HTTP server up. Internal links MUST come from here (or `/api/povison-blog/*`) — fabricating `povison.com/blog/` URLs is forbidden (they 404):

```bash
# keyword → ranked real blog articles
python3 scripts/povison-blog.py search -q "sofa bed materials" -n 5

# is this URL a real povison.com/blog/ article?
python3 scripts/povison-blog.py verify --url "https://www.povison.com/blog/buying-guide/sofa-bed-vs-sleeper-sofa-essential-differences.html"

# topic + sections → 2-3 internal links ready for articleState.links
python3 scripts/povison-blog.py recommend-links \
  --topic '{"primary_keyword":"sofa bed","secondary_keywords":["sleeper sofa"]}' \
  --sections /tmp/sections.json --limit 3
```

The sitemap is cached on disk (`.cache/blog_sitemap.xml`, TTL ~6h); pass `--refresh` to `search` to force-refresh. Same operations over HTTP via `/api/povison-blog/*`.

### Product reviews (magento2 DB, read-only)

`scripts/povison-reviews.py` exposes the magento2 review DB to the agent so Editorial Picks product cards can quote one real APPROVED buyer review (name + date + quote + star rating) instead of a fabricated testimonial. Only reads (§4 — deterministic access is tooled, no ad-hoc SQL from the model). Configure via `MAGENTO_DB_*` env in `.env`:

```bash
python3 scripts/povison-reviews.py fetch --spu 123 --limit 5 --min-rating 4
python3 scripts/povison-reviews.py summary --spu 123
```

Same operations over HTTP via `/api/povison-reviews/by-spu` + `/api/povison-reviews/summary`. The Editorial Picks Agent uses `/api/povison-reviews/by-url?url=<product URL>` instead of `by-spu`, because the product card carries the storefront URL (the storefront `id` is a non-numeric handle and the catalog API `spu` is an alphanumeric SKU like `M2-TS8125` — neither is the numeric magento `entity_id` the review DB keys on). `by-url` resolves the numeric SPU from the URL slug via `catalog_product_entity_varchar.url_key` internally. When the DB is not configured (or the URL has no matching product / no reviews) all paths return `ok=false` (not 500) so the Agent gracefully omits the review quote rather than failing.

### Editorial Picks placement style

The placement style (inline blurb vs editorial review cards) is chosen in the **正文与植入** panel (style switch at the top of the sections card) — *before* writing sections — so the section Agent can branch its working mode on the choice. `placementStyle="editorial"` makes the section Agent write body prose WITHOUT inline povison links and instead generate a standalone `POVISON Picks` H2 with exactly 3 H3 product cards (each: plain-text H3 → image wrapping the PDP link → 90-150 word blurb with a required specs paragraph → optional real review quote), plus `editorialTitle` and `editorialIntro`:

- **`editorialTitle`** — a descriptive H2 heading that combines the product category and the topic scenario, derived from `topic.primary_keyword` + `topic.angle` + outline H2s (e.g. `Best media console picks for OLED TV setup`). It is NOT the blog title; the `POVISON Picks — {topic.title}` form is only a code fallback when the Agent leaves it empty. The rendered H2 also appears in the article TOC (id `povison-picks`), inserted before Conclusion.
- **`editorialIntro`** — a 50-70 word overview paragraph that names the product category + scenario, states the selection criteria, and ends with a disclaimer: dimensions/mechanism/finishes/pricing are for reference only — refer to each product's detail page on povison.com for the most current specs and price.

The cards are saved as `products` with `status="pending"`; the operator accepts them in the product/internal-link sub-zone (below the section editor in the same panel), then `confirmPlacements()` flips `phaseDone.placements`. Switching style after sections exist marks the body stale (banner prompts re-generation) but keeps the prose. Fewer than 3 accepted products degrades to the inline path (no partial editorial block, and the TOC entry is omitted). See `references/content-guidelines.md` §编辑式评测卡.

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
| `global` | every generation | 语气、语言、品牌底线、事实核查底线（跨板块共性只写这里） |
| `outline` | `mode=outline` | H2–H3 大纲结构与 SERP 覆盖 |
| `intro` | section `type=Intro` | Introduction 写作约束（通用；榜单 Intro 细则见 listicle） |
| `h2` | section `type=H2` | 各 H2 正文写作约束（过渡段、数据、体验感等） |
| `conclusion` | section `type=Conclusion` | Conclusion 写作约束（通用；榜单 CTA 见 listicle） |
| `placements` | `mode=placements` | 产品植入与内链规则（URL 真实性合并为一条） |
| `faq` | `mode=faq` | FAQ 写作约束（榜单答案长度见 listicle） |
| `meta` | `mode=meta` | Meta Title / Description / Slug 规则 |
| `listicle` | outline + 正文各块 + placements + faq（追加） | 榜单类（Top/Best）专属结构与细则；规则自带"榜单类"前提，非榜单主题时自然失效 |
| `images` | 正文各块 + placements + meta（追加） | 配图与产品图规则 |

### Dedup principles (v2.4+)

- Cross-cutting voice / anti-keyword-stuffing / no-fabrication → **`global` only**
- Listicle-only extras (Intro H2 标题、Conclusion CTA、FAQ 30–40 词答案) → **`listicle` only**
- Catalog/blog URL authenticity → **one `placements` rule**
- Stock source + visual constraints → **one `images` rule**
- Numbers that belong to another block are referenced ("见 FAQ 板块") instead of restated

### Versioning

Rules persisted to `localStorage` carry a `version` field (current
`DEFAULT_RULES_VERSION = '2.4'`). When a stored envelope's version differs from
the current default, `applyRulesEnvelope` rebuilds from `DEFAULT_RULES` while
preserving the operator's enabled/disabled toggles on matching rule texts and
appending any user-added custom rules. This lets default rule updates roll out
to existing users without wiping their customizations.

