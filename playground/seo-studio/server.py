"""POVISON SEO Studio Bridge — single-file FastAPI backend.

Serves the Studio UI, wraps the skill's deterministic Python scripts as
background jobs, and delegates open-ended SEO work to the povison-seo
Hermes Gateway (``POST /v1/runs``).

Started by ``start.sh`` as::

    python -m uvicorn server:app --host 127.0.0.1 --port 8766

Env (set by start.sh):
    SEO_SKILL_DIR        skill root (scripts/ + templates/ + data/)
    SEO_RUNS_DIR         where per-run directories live
    SEO_STUDIO_HTML      path to the Studio HTML to serve at /
    HERMES_GATEWAY_BASE  gateway URL (default http://127.0.0.1:8644)
    HERMES_GATEWAY_KEY   gateway bearer token
    SEO_PROFILE          profile name for /api/health
    SEO_SCRIPT_TIMEOUT   per-script subprocess timeout seconds (default 600)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import sys
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from auth import feishu_h5_client, oidc_client, operator_session, operator_store
from auth.oidc_routes import router as oidc_router


def _load_dotenv() -> None:
    """Load ``.env`` from the seo-studio dir so the Bridge works even when
    started manually (not via ``start.sh``).

    Only sets vars that are NOT already in the environment (real env wins).
    This is critical for ``SEO_LLM_API_KEY`` — without it, script-path LLM
    calls (section-generate.py --mode meta/faq) silently fail and fall back
    to demo content. Also loads the profile ``.env`` if present.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here / ".env",
        Path.home() / ".hermes" / "profiles" / os.environ.get("SEO_PROFILE", "povison-seo") / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
        except Exception:
            pass


_load_dotenv()


SKILL_DIR = Path(os.environ.get("SEO_SKILL_DIR", "")).resolve() or Path.home() / ".hermes/skills/productivity/povison-seo-blog"
RUNS_DIR = Path(os.environ.get("SEO_RUNS_DIR", str(SKILL_DIR / "runs"))).resolve()
STUDIO_HTML = Path(os.environ.get("SEO_STUDIO_HTML", "")).resolve() or (Path(__file__).parent / "ui" / "index.html")
GATEWAY_BASE = os.environ.get("HERMES_GATEWAY_BASE", "http://127.0.0.1:8644")
GATEWAY_KEY = os.environ.get("HERMES_GATEWAY_KEY", "")
PROFILE = os.environ.get("SEO_PROFILE", "povison-seo")
SCRIPT_TIMEOUT = int(os.environ.get("SEO_SCRIPT_TIMEOUT", "600"))
SCRIPTS = SKILL_DIR / "scripts"
TEMPLATE = SKILL_DIR / "templates" / "blog-post-template.html"

app = FastAPI(title="POVISON SEO Studio Bridge", version="1.0")
app.include_router(oidc_router)

# Local dev: allow any origin (file:// standalone + http://127.0.0.1:8766).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PUBLIC_PREFIXES = ("/auth",)
_PUBLIC_PATHS = {"/", "/api/health"}


def _auth_required() -> bool:
    """Whether /api/* (except health) requires a login session.

    ``SEO_STUDIO_REQUIRE_LOGIN``:
      - ``0``/``false``: never enforce (open mode, dev).
      - ``1``/``true``: always enforce.
      - ``auto`` (default): enforce when OIDC OR Feishu H5 is configured.
    """
    val = os.environ.get("SEO_STUDIO_REQUIRE_LOGIN", "auto").strip().lower()
    if val in ("0", "false", "no"):
        return False
    if val in ("1", "true", "yes"):
        return True
    return oidc_client.is_configured() or feishu_h5_client.is_configured()


@app.on_event("startup")
def _startup_auth() -> None:
    import logging
    logger = logging.getLogger("seo-studio")
    oidc_on = oidc_client.is_configured()
    h5_on = feishu_h5_client.is_configured()
    if oidc_on:
        try:
            oidc_client.discovery()
        except oidc_client.OIDCError as exc:
            logger.error("OIDC discovery failed: %s", exc)
    if oidc_on or h5_on:
        try:
            operator_store.init_db()
        except Exception as exc:
            logger.error("operator_store init failed: %s", exc)
        if not operator_session._secret():
            logger.error(
                "SEO_STUDIO_SESSION_SECRET is empty but login is enabled — "
                "sessions cannot be issued. Set a long random value."
            )
    if not oidc_on and not h5_on:
        logger.warning(
            "OIDC + H5 both unconfigured — Studio running in OPEN mode (no auth)."
        )


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    if path.startswith("/api/") and _auth_required():
        sess = operator_session.verify(request.cookies.get(operator_session.COOKIE_NAME))
        if not sess:
            return JSONResponse({"detail": "unauthenticated"}, status_code=401)
        request.state.operator = sess
    else:
        request.state.operator = {"oidc_sub": "studio", "name": "操作员"}
    return await call_next(request)

# ---- background job registry -------------------------------------------------
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

import db as _db  # SQLite workflow record layer
import wp_publish as _wp  # WordPress draft export (reuses wordpress_mcp)


def _new_run_dir(label: str | None) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    name = f"run-{stamp}-{short}"
    d = RUNS_DIR / name
    d.mkdir(parents=True, exist_ok=False)
    if label:
        (d / ".label").write_text(label, encoding="utf-8")
    return d


def _run_id(d: Path) -> str:
    return d.name


def _resolve_run(rid: str) -> Path:
    d = RUNS_DIR / rid
    if not d.exists():
        raise HTTPException(status_code=404, detail=f"run not found: {rid}")
    return d


def _spawn_job(kind: str, cmd: list[str], cwd: Path, rid: str = "", on_done=None, on_finish=None) -> str:
    """Run a subprocess in a thread; return job_id immediately.

    ``on_done`` runs only on success (rc==0). ``on_finish`` runs in the
    ``finally`` block with ``(job_id, final_status, returncode)`` regardless of
    success/failure — used by callers to reset DB step status after a script job
    (so the task list reflects "running" during the job and is restored after).
    """
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {"kind": kind, "status": "running", "started_at": time.time(), "run_id": rid}

    def worker() -> None:
        rc: int | None = None
        err = ""
        try:
            # Inherit Bridge env (incl. SEO_LLM_* loaded by _load_dotenv at import).
            child_env = os.environ.copy()
            if kind == "section-generate" and not child_env.get("SEO_LLM_API_KEY", "").strip():
                import sys as _sys
                print(
                    "  [seo-studio] SEO_LLM_API_KEY not set — script LLM steps (meta/faq/…) "
                    "will fall back to demo templates. Set it in playground/seo-studio/.env "
                    "and restart Bridge.",
                    file=_sys.stderr,
                )
            proc = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
                env=child_env,
            )
            rc = proc.returncode
            with _JOBS_LOCK:
                job = _JOBS[job_id]
                job["returncode"] = proc.returncode
                job["stdout"] = proc.stdout[-4000:]
                job["stderr"] = proc.stderr[-4000:]
                if proc.returncode == 0:
                    job["status"] = "succeeded"
                else:
                    job["status"] = "failed"
                    err = (proc.stderr or proc.stdout or "non-zero exit").strip()[:1000] or f"exit {proc.returncode}"
                    job["error"] = err
            if on_done and proc.returncode == 0:
                on_done()
        except subprocess.TimeoutExpired:
            err = f"timeout after {SCRIPT_TIMEOUT}s"
            with _JOBS_LOCK:
                _JOBS[job_id].update(status="failed", error=err)
        except Exception as e:
            err = str(e)[:1000]
            with _JOBS_LOCK:
                _JOBS[job_id].update(status="failed", error=err)
        finally:
            if rid:
                status = _JOBS[job_id]["status"]
                try:
                    _db.record_audit(rid, "script", kind, " ".join(cmd[2:6]), status, rc, err)
                except Exception:
                    pass
            if on_finish is not None:
                try:
                    on_finish(job_id, _JOBS[job_id]["status"], rc)
                except Exception:
                    pass

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def _script_step_track(rid: str, step_num: int, *, mark_done_on_success: bool = False):
    """Mark a DB-backed task's step as `running` for an in-flight script job and
    return an `on_finish(job_id, final_status, rc)` callback for `_spawn_job`.

    Script-path endpoints (keyword discover/enrich, brainstorm, section-generate)
    previously did NOT mark the step/task running in the DB — so the task list
    showed idle during generation and the client could not restore the progress
    bar after a task switch. This fixes both: the running status lets the task
    list show 运行中 and lets `resumeTaskAgentPolling` dispatch to
    `resumeScriptJobPolling` (via the `.script_job` marker).

    The caller writes the `.script_job` marker after `_spawn_job` returns.
    `on_finish` clears it; on failure it marks the step `error` (which settles
    the task status to idle via `set_step_status`). On success, the caller's
    `on_done` callback is expected to mark the step `done` (e.g.
    `record_keywords`/`record_topics`/`record_article_state`). For precursors
    with no `on_done` (keyword discover), pass `mark_done_on_success=True` so
    the step is not left stuck `running`. No-op for non-task runs."""
    is_task = str(rid).startswith("task-")
    if is_task:
        try:
            # clear_agent_run_id=True so a stale agent_run_id from a previous
            # agent-path run does not survive — the client uses an empty
            # agent_run_id to detect a script job and dispatch to
            # resumeScriptJobPolling instead of polling the (dead) gateway run.
            _db.set_step_status(rid, step_num, "running", clear_agent_run_id=True)
            _db.mark_task_running(rid)
        except Exception:
            pass

    def on_finish(job_id, final_status, rc):
        if not is_task:
            return
        try:
            marker = RUNS_DIR / rid / ".script_job"
            if marker.exists():
                marker.unlink()
        except Exception:
            pass
        try:
            if final_status == "succeeded" and mark_done_on_success:
                _db.set_step_status(rid, step_num, "done")
            elif final_status != "succeeded":
                _db.set_step_status(rid, step_num, "error")
        except Exception:
            pass

    return is_task, on_finish


def _write_script_job_marker(rid: str, job_id: str) -> None:
    """Persist the in-flight script job_id so the client can resume polling it
    after a task switch (see `resumeScriptJobPolling` + the
    `/api/tasks/{id}/active-script-job` endpoint)."""
    try:
        (RUNS_DIR / rid / ".script_job").write_text(job_id, encoding="utf-8")
    except Exception:
        pass


def _py() -> str:
    return sys.executable


# ---- health ------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request) -> dict:
    scripts_ok = (SCRIPTS / "section-generate.py").exists() and (SCRIPTS / "validate-article.py").exists()
    from auth import feishu_setup

    feishu_hint = feishu_setup.build_setup_hint(request) if feishu_h5_client.is_configured() else None
    return {
        "ok": True,
        "profile": PROFILE,
        "scripts_ok": scripts_ok,
        "gateway_key_set": bool(GATEWAY_KEY),
        "seo_llm_configured": bool(os.environ.get("SEO_LLM_API_KEY", "").strip()),
        "seo_llm_model": os.environ.get("SEO_LLM_MODEL", ""),
        "skill_dir": str(SKILL_DIR),
        "runs_dir": str(RUNS_DIR),
        "db": _db.stats(),
        "oidc_configured": oidc_client.is_configured(),
        "feishu_h5_enabled": feishu_h5_client.is_configured(),
        "feishu_app_id": feishu_h5_client.app_id() if feishu_h5_client.is_configured() else "",
        "auth_required": _auth_required(),
        "feishu_setup": feishu_hint,
    }


# ---- tasks (DB source of truth) ----------------------------------------------


def _task_or_404(task_id: str) -> dict:
    t = _db.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return t


def _step_num_for_agent(step: str) -> int:
    if step in ("brainstorm",):
        return 2
    if step in ("keywords", "discover", "enrich"):
        return 1
    return 3  # serp/outline/section/faq/meta/placements


# ---- placement URL validation (root-cause fix for 404 placements) -------------

_PDP_RE = re.compile(r"^https?://(?:[\w-]+\.)*povison\.com/[^?#]*\.html(?:[?#]|$)", re.I)
_BLOG_RE = re.compile(r"^https?://(?:[\w-]+\.)*povison\.com/blog/[^?#]*\.html(?:[?#]|$)", re.I)
# Internal links may also point at povison.com category/collection landing pages
# (SKILL.md §内链: "从 povison.com/blog/ 选相关文章（或合适类目页）"). These are
# real internal pages, not fabricated — the liveness guard is the backstop.
_COLLECTION_RE = re.compile(r"^https?://(?:[\w-]+\.)*povison\.com/collections/[^?#/]+(?:[?#]|/?$)", re.I)
_POVISON_HOST_RE = re.compile(r"^(?:[\w-]+\.)*povison\.com$", re.I)
# Markdown link: [text](url). Used to detect inline placements baked into prose
# (merged section+placement flow) and to strip rejected ones at assembly time.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", re.I)


# ---- section sub-step guidance (branched on placementStyle) ----------------
#
# The operator now picks a placement style in the 正文生成 phase BEFORE writing
# sections, so the section Agent must branch its working mode on that choice:
#   - inline: weave 1-2 product links + 1-2 internal links INTO the section prose
#     (the merged-flow behaviour that existed before this change).
#   - editorial: write sections WITHOUT any inline povison links, then generate
#     the standalone POVISON Picks H2 with EXACTLY 3 review cards + editorialTitle
#     + editorialIntro. The placements panel then only reviews/accepts the 3
#     pending cards; it must NOT set phaseDone.placements (confirmPlacements owns
#     that flag, and cards start as 'pending').
# Extracted as module-level constants so tests can assert the branch without
# spinning up the agent-run endpoint.
_SECTION_SUBSTEP_GUIDANCE_INLINE = (
    "Generate body sections for the confirmed outline. "
    "Each articleState.sections[] item MUST use this schema: "
    "{id, type, title, content, status, image_queries?, images?, subheads?, transition?} where "
    "type is one of 'Intro' (capitalized) for the intro, 'h2' for body sections, "
    "'Conclusion' (capitalized) for the conclusion; title is the section heading text "
    "(empty string for Intro); content is the markdown body; status='ready' when done. "
    "Set articleState.phaseDone.sections = true when all sections have content.\n"
    "MERGED PLACEMENTS (write inline links while drafting — do NOT defer to a later placements run):\n"
    "1) FIRST fetch a candidate pool of REAL povison URLs by calling "
    "`python3 scripts/povison-catalog.py recommend --topic '<JSON>' --sections <file> --limit 5` "
    "AND `python3 scripts/povison-blog.py recommend-links --topic '<JSON>' --sections <file> --limit 5` "
    "(or POST http://127.0.0.1:8766/api/povison-products/recommend and /api/povison-blog/recommend-links "
    "with {topic, sections, limit}). Store the result in articleState._candidatePool for audit.\n"
    "2) As you write each section's content, weave 1-2 product links and 1-2 internal links INLINE as "
    "markdown `[anchor](url)` using ONLY URLs from the pool. For a product, write a short 40-60 word "
    "advice paragraph that links the product name to its PDP. NEVER invent, shorten, or rehost a URL — "
    "if no pool entry fits, write the section with zero placements rather than fabricate one.\n"
    "3) Do NOT output a placements_used field. After you save, a server-side hook derives "
    "articleState.products/links deterministically by parsing the markdown links out of each "
    "section.content and matching them back to the pool. You do not need to populate products/links "
    "yourself, but you MAY set them from the pool as a best-effort hint (the hook is the source of truth).\n"
    "4) If the Bridge is unreachable (no pool), write sections WITHOUT inline links — the legacy "
    "assembly path will handle placements for that task.\n"
    "VOICE / HUMANIZE (REQUIRED before saving each section):\n"
    "1) After drafting each section's content, call `skill_view('humanizer')` and apply its "
    "anti-AI-pattern rules to rewrite the prose so it sounds like a real person wrote it — "
    "strip 'stands as a testament', 'underscores the importance', 'in today's fast-paced world', "
    "'it's not just X, it's Y', em-dash asides, and other tells the skill lists.\n"
    "2) Keep POVISON buying-guide credibility: first-person experience and opinions are welcome, "
    "but stay trustworthy and specific — no stand-up bits, no slang, no manufactured drama. "
    "Vary sentence length, prefer concrete details over filler, and let one or two genuine "
    "opinions through per section.\n"
    "3) Preserve all SEO structure: keep H2/H3 headings, tables, data citations, inline product/internal "
    "links, image_queries, and section boundaries intact — humanize the prose, not the structure.\n"
    "IMAGES (P0+P1 — structured queries + stock API candidate pool):\n"
    "1) For each H2 that warrants a visual, FIRST write section.image_queries = "
    "[2-3 concrete ENGLISH search phrases]. Good: 'couple arranging living room sofa', "
    "'modern apartment furniture inventory checklist'. Bad: the whole article title, "
    "vague terms like 'moving', 'home', 'lifestyle', 'together'.\n"
    "2) Search the stock API — do NOT invent URLs and do NOT browser-scrape random pages. "
    "Prefer: `python3 scripts/search-stock-images.py -q \"<query>\" -n 5 -o /tmp/stock.json` "
    "(or POST http://127.0.0.1:8766/api/stock-images/search with {query, per_page:5}). "
    "Try queries in order until you get candidates.\n"
    "3) Pick ONE candidate from the returned pool whose photo clearly shows furniture, "
    "an interior room, or a layout relevant to THIS section. Set "
    "section.images = [{url, alt, caption, credit}] using the candidate's url/alt/credit. "
    "caption = short English figure caption matching the section.\n"
    "4) HARD RULES — skip the section (images=[]) if no good match:\n"
    "   - Source MUST be Pixabay or Openverse from the API pool. NEVER POVISON product photos here.\n"
    "   - MUST depict furniture / room interior / layout. FORBIDDEN: moving boxes close-ups, "
    "     generic handshakes, abstract textures, outdoor scenes unrelated to the section, "
    "     pure portraits with no furniture.\n"
    "   - Do NOT duplicate an image URL across sections.\n"
    "   - Prefer fewer accurate images over forcing a weak match."
)

_SECTION_SUBSTEP_GUIDANCE_EDITORIAL = (
    "Generate body sections for the confirmed outline in EDITORIAL PICKS mode. "
    "Each articleState.sections[] item MUST use the schema: "
    "{id, type, title, content, status, image_queries?, images?, subheads?, transition?} where "
    "type is one of 'Intro' (capitalized), 'h2', 'Conclusion' (capitalized); title is the heading "
    "text (empty string for Intro); content is the markdown body; status='ready' when done.\n"
    "EDITORIAL MODE — BODY HAS NO INLINE PLACEMENTS:\n"
    "1) Write Intro / each H2 / Conclusion WITHOUT weaving ANY povison.com inline markdown links into "
    "the prose. Editorial mode puts all product placements in a standalone POVISON Picks H2 rendered "
    "server-side from articleState.products — inline links in body sections would double up.\n"
    "   HARD RULE — no product-feature blurb in body sections: the POVISON Picks cards are the ONLY "
    "place a POVISON product gets showcased. Body sections MUST NOT contain product-feature/spec blurb "
    "paragraphs — no dimensions, mechanism, material, construction-quality, or assembly descriptions of "
    "a specific POVISON product (e.g. 'The Ansel ... handles this well. Its fluted walnut doors have "
    "integrated pulls ...'). That copy belongs exclusively on the editorial card's `blurb`. A product "
    "may be named in body prose ONLY as a brief passing plain-text reference (one short sentence, no "
    "link, no spec detail) when contextually unavoidable; if a paragraph reads like a product review or "
    "sales pitch, delete it and keep the section about the general topic. Do NOT write body sections "
    "that double as product showcases.\n"
    "2) Do NOT call povison-catalog / povison-blog for inline candidates during section drafting. "
    "Do NOT set articleState._candidatePool. Do NOT populate articleState.links (editorial mode "
    "carries no internal links in the body).\n"
    "3) Set articleState.phaseDone.sections = true when all sections have content.\n\n"
    "EDITORIAL PICKS — GENERATE THE 3 CARDS + H2 TITLE/INTRO (after all sections are written):\n"
    "1) Set articleState.editorialIntro ONLY IF it is currently empty — a 50-70 word overview "
    "paragraph that (a) names the product category and the scenario/room the article addresses "
    "(derived from topic.primary_keyword + outline H2s), (b) states the criteria used to pick these "
    "3 (e.g. footprint, assembly, materials), and (c) ends with a disclaimer sentence: 'Dimensions, "
    "mechanism, finishes, and pricing shown below are for reference only — please refer to each "
    "product's detail page on povison.com for the most current specs and price.' Do NOT overwrite a "
    "non-empty editorialIntro (the operator may have edited it).\n"
    "2) Set articleState.editorialTitle ONLY IF it is currently empty — a descriptive H2 heading "
    "that combines the product category and the topic scenario (NOT the blog title). Derive it from "
    "topic.primary_keyword + topic.angle + outline H2s, formatted like 'Best {product category} "
    "picks for {scenario}' (e.g. 'Best media console picks for OLED TV setup', 'Best sectional "
    "sofas for small-space living'). Do NOT use the 'POVISON Picks — {topic.title}' default — that "
    "is only a code fallback when the Agent leaves it empty. Do NOT overwrite a non-empty "
    "editorialTitle.\n"
    "3) Call `povison-catalog.py recommend --topic '<JSON>' --limit 3` (or POST "
    "http://127.0.0.1:8766/api/povison-products/recommend with {topic, limit:3}) to get EXACTLY 3 "
    "real product candidates. Each MUST have a real PDP URL (`/<slug>.html?variant=<id>`). NEVER "
    "fabricate a URL — if fewer than 3 good matches, write fewer (the placements panel will flag the "
    "shortfall; the operator can re-pick).\n"
    "4) For EACH of the 3 products, fill a card: {name, url, image (Detail API), "
    "sectionId='editorial-picks', status='pending', blurb, specs?, reviewQuote?} where:\n"
    "   - blurb: 90-150 words, 1-3 paragraphs, MUST include a specs/mechanism paragraph "
    "(dimensions + mechanism + material + colors). Optionally add a scene paragraph, a buyer-review "
    "quote paragraph, and a value summary.\n"
    "   - reviewQuote: the product's storefront `url` is the ONLY stable id on the card (the product "
    "`id` is a non-numeric storefront handle, NOT a magento SPU — do NOT pass it to by-spu). Call "
    "GET http://127.0.0.1:8766/api/povison-reviews/by-url?url=<product.url>&min_rating=4&limit=1 — it "
    "resolves the magento numeric SPU from the URL slug internally and returns ONE real APPROVED "
    "buyer review. Use {reviewer:nickname, date, quote:detail, rating}. If the API returns ok=False "
    "or empty (no reviews for this product / DB not configured), OMIT reviewQuote — NEVER fabricate a "
    "review quote.\n"
    "   - specs: {dimensions, mechanism, material, colors} from the Detail API.\n"
    "   - warranty: only if surfaced by the PDP/Detail API; omit otherwise.\n"
    "5) Set articleState.products = [the 3 cards]. Do NOT set articleState.phaseDone.placements (keep "
    "it false) and do NOT set articleState.placementsConfirmed — the cards are 'pending' and must be "
    "accepted by the operator in the 产品与内链 panel before confirmPlacements() flips that flag. "
    "Do NOT populate articleState.links.\n"
    "HARD RULES: products.length should be exactly 3 (or fewer if the catalog had no good matches); "
    "H3 headings are PLAIN TEXT (no link); the PDP link goes on the product IMAGE only; blurb 90-150 "
    "words with a specs paragraph required.\n\n"
    "VOICE / HUMANIZE + IMAGES: apply the same humanizer + stock-image rules as inline mode (call "
    "skill_view('humanizer') before saving each section; image_queries + Pixabay/Openverse pool only, "
    "no POVISON product photos in body sections)."
)


# Module-level placements sub-step guidance (extracted so tests can assert the
# editorial branch is present without spinning up the agent-run endpoint).


def _section_guidance_for_task(task_dir: Path) -> str:
    """Pick the section sub-step guidance branch based on the article's
    ``placementStyle`` read from the DB step-3 data materialized into the task
    context dir. Returns the editorial guidance when the operator chose
    editorial mode, else the inline merged-placements guidance. Defaults to
    inline for fresh tasks with no step-3 data yet.
    """
    try:
        state_file = task_dir / "article-state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("placementStyle") == "editorial":
                return _SECTION_SUBSTEP_GUIDANCE_EDITORIAL
    except Exception:
        pass
    return _SECTION_SUBSTEP_GUIDANCE_INLINE


_PLACEMENTS_SUBSTEP_GUIDANCE = (
    "RE-PICK / RE-RESOLVE placements (UI button「重新挑选植入候选」). "
    "This is a live Hermes Agent run — NEVER invent URLs and NEVER use static demo catalog "
    "seeds (placement-catalog.json / demo_placements). Always call the Bridge recommend APIs "
    "(or equivalent CLI) for real povison.com PDP / blog URLs.\n"
    "The body sections were already written (inline mode weaves REAL povison URLs into prose; "
    "editorial mode keeps products in the standalone POVISON Picks cards). Your job is to "
    "review/confirm and, if needed, re-derive or re-pick the structured placement cards.\n"
    "1) Scan each articleState.sections[].content for markdown `[anchor](url)` links whose url is a "
    "real povison.com PDP (contains `.html` and `?variant=`), blog article "
    "(`https://www.povison.com/blog/.../.html`), or collection page. Build articleState.products and "
    "articleState.links from these: each item gets {id, status:'pending', name/anchor, url, "
    "sectionId, fit_score/reasons when known from articleState._candidatePool}.\n"
    "2) If sections have NO inline links (legacy task written before the merge), fall back to the "
    "old generate path: call `povison-catalog.py recommend` and `povison-blog.py recommend-links` "
    "(limit 2 / 3) and write the results into products/links. Real PDP URLs contain `.html` + "
    "`?variant=`; real blog URLs start with `https://www.povison.com/blog/` and end with `.html`. "
    "NEVER fabricate a URL — if the API returns no good match, write fewer (0-1) rather than a 404.\n"
    "3) REPLACE articleState.products and articleState.links entirely with the re-derived set "
    "(the operator may have deleted entries before re-running). Set "
    "articleState.phaseDone.placements = true once the cards reflect the prose.\n"
    "NOTE: the merged section path is preferred — this standalone placements run is mainly for "
    "re-resolving after the operator edits prose or deletes cards. A server-side hook also "
    "re-derives products/links from prose on every step-3 save, so this run is a confirmation pass.\n\n"
    "EDITORIAL PICKS BRANCH (when articleState.placementStyle == 'editorial'):\n"
    "This branch REPLACES the inline re-resolution above. PRIMARY generation of the 3 "
    "editorial cards now happens in the SECTION step (the operator picks the style before "
    "writing sections, and the section Agent generates the cards + editorialTitle + "
    "editorialIntro there). This placements run is a RE-PICK / confirmation pass: use it "
    "ONLY when the operator wants different products than the section step produced, or to "
    "re-fill missing blurb/specs/reviewQuote on the existing 3 cards.\n"
    "1) Do NOT overwrite a non-empty articleState.editorialTitle / editorialIntro (the operator "
    "or the section step already set them). Only fill them if they are empty. When filling "
    "editorialTitle, write a descriptive H2 combining product category + topic scenario (e.g. "
    "'Best media console picks for OLED TV setup'), NOT the blog title. When filling "
    "editorialIntro, write a 50-70 word overview paragraph (criteria + scenario) ending with the "
    "PDP disclaimer: 'Dimensions, mechanism, finishes, and pricing shown below are for reference "
    "only — please refer to each product's detail page on povison.com for the most current specs "
    "and price.'\n"
    "2) Call `povison-catalog.py recommend --topic '<JSON>' --limit 3` (or POST "
    "http://127.0.0.1:8766/api/povison-products/recommend) to get EXACTLY 3 real product candidates. "
    "Each product MUST have a real PDP URL (`/<slug>.html?variant=<id>`). NEVER fabricate URLs.\n"
    "3) For EACH of the 3 products, fill: {name, url, image (Detail API), sectionId='editorial-picks', "
    "status='pending', blurb (90-150 words, 1-3 paragraphs, MUST include a specs/mechanism paragraph: "
    "dimensions + mechanism + material + colors), and OPTIONALLY:\n"
    "   - reviewQuote: the product's storefront `url` is the ONLY stable id on the card (the product "
    "`id` is a non-numeric storefront handle, NOT a magento SPU — do NOT pass it to by-spu). Call "
    "GET http://127.0.0.1:8766/api/povison-reviews/by-url?url=<product.url>&min_rating=4&limit=1 — it "
    "resolves the magento numeric SPU from the URL slug internally and returns ONE real APPROVED "
    "buyer review. Use {reviewer:nickname, date, quote:detail, rating}. If the reviews API returns "
    "ok=False or empty, OMIT reviewQuote — NEVER fabricate a review quote.\n"
    "   - specs: {dimensions, mechanism, material, colors} from the Detail API.\n"
    "   - warranty: only if surfaced by the PDP/Detail API; omit otherwise.\n"
    "4) Do NOT re-resolve inline links from section prose (editorial mode has no inline placements). "
    "Do NOT populate articleState.links (the editorial H2 carries no internal links).\n"
    "5) Set articleState.products = [the 3 cards] with status='pending'. Do NOT set "
    "articleState.phaseDone.placements and do NOT set articleState.placementsConfirmed — the cards "
    "must be accepted by the operator in the 产品与内链 panel before confirmPlacements() flips that "
    "flag. Setting phaseDone.placements=true here would skip the review panel and jump to FAQ while "
    "the cards are still pending.\n"
    "HARD RULES: products.length should be exactly 3 (or fewer if the catalog had no good matches); "
    "H3 headings are PLAIN TEXT (no link); the PDP link goes on the product IMAGE only; blurb 90-150 "
    "words with a specs paragraph required."
)


def _is_inline_placement_url(url: str) -> bool:
    """True when url is a povison.com PDP, blog article, or collection page."""
    if not url:
        return False
    host = (urlparse(url).hostname or "")
    if not _POVISON_HOST_RE.match(host):
        return False
    return bool(_PDP_RE.match(url) or _BLOG_RE.match(url) or _COLLECTION_RE.match(url))


def _has_inline_povison_links(content: str) -> bool:
    """True when raw section content already contains inline markdown links to
    povison.com — i.e. the merged section+placement flow wrote them. Used by
    ``_prepare_section_content`` to pick the new assembly path vs the legacy
    inline_replace + Related-fallback + orphan-blurb path.
    """
    if not content:
        return False
    return any(_is_inline_placement_url(u) for _, u in _MD_LINK_RE.findall(content))


def _validate_placement_urls(data: dict) -> list[dict]:
    """Flag fabricated product / internal-link URLs.

    Returns a list of warnings: ``[{kind, idx, url, problem}]``. Empty = clean.
    Product URLs must be povison PDPs (``.html`` + povison host); link URLs must
    be povison blog articles (``/blog/<...>.html``) or category landing pages
    (``/collections/<slug>``). Fabricated URLs are the root cause of 404
    placements — this validator blocks confirming placements until they're
    fixed or sourced from the catalog/blog APIs. The HTTP liveness guard
    (``placementUrlCheck``) is the final backstop for any URL that passes the
    pattern check but still 404s.
    """
    warnings: list[dict] = []
    if not isinstance(data, dict):
        return warnings
    for i, p in enumerate(data.get("products") or []):
        if not isinstance(p, dict):
            continue
        url = (p.get("url") or "").strip()
        if not url:
            warnings.append({"kind": "product", "idx": i, "url": "", "problem": "product.url is empty"})
            continue
        host = urlparse(url).hostname or ""
        if not _POVISON_HOST_RE.match(host):
            warnings.append({"kind": "product", "idx": i, "url": url, "problem": f"product.url host is not povison.com ({host})"})
            continue
        if not _PDP_RE.match(url):
            warnings.append({"kind": "product", "idx": i, "url": url, "problem": "product.url is not a real PDP (must end with .html; real PDPs look like /<slug>.html?variant=<id>) — likely fabricated, will 404"})
    for i, l in enumerate(data.get("links") or []):
        if not isinstance(l, dict):
            continue
        url = (l.get("url") or "").strip()
        if not url:
            warnings.append({"kind": "link", "idx": i, "url": "", "problem": "link.url is empty"})
            continue
        host = urlparse(url).hostname or ""
        if not _POVISON_HOST_RE.match(host):
            warnings.append({"kind": "link", "idx": i, "url": url, "problem": f"link.url host is not povison.com ({host})"})
            continue
        if not (_BLOG_RE.match(url) or _COLLECTION_RE.match(url)):
            warnings.append({"kind": "link", "idx": i, "url": url, "problem": "link.url is not a real povison.com blog article (/blog/.../*.html) or category page (/collections/<slug>) — likely fabricated, will 404"})
    return warnings


# ---- article-state field protection (prevent intermediate agent saves from
# wiping already-generated body content) ---------------------------------------
#
# The Agent persists step-3 (articleState) across multiple seo_save_step_data
# calls within a single run (e.g. placements: write products/links, then
# post-process, then save again). An intermediate save can briefly carry an
# empty `sections`/`faqs`/`outline`/... field, and if the UI reloads (or the
# operator switches tasks) at that instant, the already-generated body would
# appear lost. This guard backfills any "content" field that the incoming
# save would reduce to empty with the value already in the DB, so a later
# sub-step save can never clobber an earlier sub-step's output.
#
# Scope is deliberately narrow:
#   - Only applies to step 3 (articleState).
#   - Only triggers when the incoming field is EMPTY (None/[]/{}/"") AND the
#     DB already has a non-empty value — it never overwrites a real new value.
#   - `products` and `links` are intentionally NOT protected: the operator may
#     delete them (empty is meaningful) and the placements Agent is instructed
#     to REPLACE them wholesale.

_ARTICLE_CONTENT_FIELDS = (
    # field name, "is empty" predicate
    ("sections", lambda v: not isinstance(v, list) or len(v) == 0),
    ("outline", lambda v: not isinstance(v, list) or len(v) == 0),
    ("faqs", lambda v: not isinstance(v, list) or len(v) == 0),
    ("serp", lambda v: not isinstance(v, dict) or not v),
    ("meta", lambda v: not isinstance(v, dict) or not v),
    ("previewHtml", lambda v: not isinstance(v, str) or v == ""),
    ("validation", lambda v: not isinstance(v, dict) or not v),
    # Editorial Picks state — protected so an intermediate Agent save that omits
    # them can't silently flip the article back to inline mode or drop the
    # operator-edited H2 title/intro (see plan §E). Strings default to "" when
    # absent; placementStyle defaults to "inline" downstream, so an empty/missing
    # value IS meaningful (means "unset, treat as inline") and must be backfilled
    # from the DB rather than overwrite a real "editorial" choice.
    ("placementStyle", lambda v: not isinstance(v, str) or v == ""),
    ("editorialTitle", lambda v: not isinstance(v, str) or v == ""),
    ("editorialIntro", lambda v: not isinstance(v, str) or v == ""),
)


def _backfill_empty_content_fields(data: dict, db_data: dict | None) -> int:
    """Backfill empty content fields in ``data`` from ``db_data``.

    Returns the number of fields backfilled (0 = no change). Only fields listed
    in ``_ARTICLE_CONTENT_FIELDS`` are considered; ``products``/``links`` are
    untouched by the base loop. Mutates ``data`` in place.

    EDITORIAL PRODUCTS GUARD: in editorial mode the section step generates the 3
    review cards into ``products``. An intermediate Agent save that omits
    ``products`` (or sends ``[]``) would wipe those cards — and unlike
    sections/outline there is no other source to re-derive them (the editorial
    short-circuit in ``_post_save_step3_inline_placements`` skips inline
    re-derivation). So in editorial mode we backfill an empty/missing ``products``
    from the DB so a later sub-step save can never clobber the section step's
    cards. ``links`` is intentionally left empty in editorial mode (no body
    internal links), so it is NOT backfilled.
    """
    if not isinstance(data, dict) or not isinstance(db_data, dict):
        return 0
    n = 0
    for field, is_empty in _ARTICLE_CONTENT_FIELDS:
        incoming = data.get(field)
        if is_empty(incoming):
            existing = db_data.get(field)
            if not is_empty(existing):
                data[field] = existing
                n += 1
    # Editorial products guard (see docstring).
    if (data.get("placementStyle") or "inline") == "editorial":
        incoming_products = data.get("products")
        if not isinstance(incoming_products, list) or len(incoming_products) == 0:
            existing_products = db_data.get("products")
            if isinstance(existing_products, list) and len(existing_products) > 0:
                data["products"] = existing_products
                n += 1
    return n


def _post_save_step3_inline_placements(data: dict) -> list[dict]:
    """Server-side post-save hook for step 3 (C1): derive placements from inline
    markdown links baked into ``sections[].content`` by the merged flow.

    Runs on EVERY step-3 save, so it covers both the Agent path (which writes
    articleState directly via seo_save_step_data and has no Python post-process)
    and the script path. It:

    1. Scans each section's content for markdown ``[text](povison url)`` links.
    2. Validates each povison URL shape; invented/malformed povison URLs are
       SANITIZED out of the prose (unwrapped to plain text — §4 backstop, matches
       the script path's ``_resolve_and_backfill``) and flagged as warnings (C2).
    3. Backfills ``data["products"]``/``data["links"]`` from the inline links, gated
       by a ``_placementsBackfilled`` sentinel so an operator's "delete all" is
       respected (C3): backfill happens only ONCE — the first time the arrays are
       empty AND the sentinel is unset. Once placements are populated (by the
       script, the Agent, or this hook), the sentinel is set and subsequent
       clears are treated as intentional. Enriches entries from
       ``data["_candidatePool"]`` when present (name, image, score).

    Mutates ``data`` in place. Returns a list of warning dicts (empty = clean).
    """
    if not isinstance(data, dict):
        return []
    # Editorial Picks short-circuit (plan §F): in editorial mode the placements
    # are the 3 product cards the Agent wrote explicitly into articleState.products
    # (with reviewQuote/specs/warranty), NOT inline links baked into section prose.
    # Re-running the inline-link scan here would mis-derive products/links from
    # any incidental povison citation in the body (e.g. a data-source PDP) and
    # clobber the editorial cards. URL-pattern validation (_validate_placement_urls)
    # and the HTTP liveness guard still run independently upstream of this hook,
    # so the double-gate on fabricated/dead URLs is preserved.
    if (data.get("placementStyle") or "inline") == "editorial":
        return []
    warnings: list[dict] = []
    sections = data.get("sections") or []
    pool = data.get("_candidatePool") or {}
    pool_products = {p.get("url"): p for p in (pool.get("products") or []) if isinstance(p, dict) and p.get("url")}
    pool_links = {l.get("url"): l for l in (pool.get("links") or []) if isinstance(l, dict) and l.get("url")}

    derived_products: list[dict] = []
    derived_links: list[dict] = []
    bad_urls: list[str] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        sid = sec.get("id") or ""
        content = sec.get("content") or ""
        seen_p: set[str] = set()
        seen_l: set[str] = set()
        for anchor, url in _MD_LINK_RE.findall(content):
            url = url.strip()
            host = (urlparse(url).hostname or "")
            if not _POVISON_HOST_RE.match(host):
                continue  # external citation — allowed
            if not _is_inline_placement_url(url):
                bad_urls.append(url)
                continue
            # Classify: blog articles + collection pages are internal LINKS;
            # other povison .html pages are product PDPs. Check blog/collection
            # FIRST because _PDP_RE also matches /blog/.../.html paths.
            if (_BLOG_RE.match(url) or _COLLECTION_RE.match(url)) and url not in seen_l:
                seen_l.add(url)
                l = pool_links.get(url) or {}
                derived_links.append({
                    "id": l.get("id") or f"l{len(derived_links)+1}",
                    "status": "pending",
                    "anchor": anchor,
                    "url": url,
                    "sectionId": sid,
                    "score": l.get("score"),
                    "reasons": l.get("reasons") or "",
                    "title_guess": l.get("title_guess") or "",
                    "category": l.get("category") or "",
                    "inline": True,
                })
            elif _PDP_RE.match(url) and url not in seen_p:
                seen_p.add(url)
                p = pool_products.get(url) or {}
                derived_products.append({
                    "id": p.get("id") or f"p{len(derived_products)+1}",
                    "status": "pending",
                    "name": p.get("name") or anchor,
                    "url": url,
                    "image": p.get("image") or "",
                    "sectionId": sid,
                    "blurb": "",
                    "fit_score": p.get("fit_score"),
                    "fit_reasons": p.get("fit_reasons") or "",
                    "sku": p.get("sku") or "",
                    "inline": True,
                })

    # #3 / #8: sanitize prose — unwrap invented/malformed povison URLs to plain
    # text so they never ship to preview/WordPress. Matches the script path's
    # _resolve_and_backfill §4 backstop. (Valid povison links and external
    # citations are kept.)
    if bad_urls:
        bad_set = set(bad_urls)
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec["content"] = _MD_LINK_RE.sub(
                lambda m: m.group(1) if m.group(2).strip() in bad_set else m.group(0),
                sec.get("content") or "",
            )

    # #1: gate backfill on the _placementsBackfilled sentinel so an operator's
    # deliberate "delete all" is not undone. Backfill runs only ONCE — when the
    # arrays are empty AND the sentinel is unset. The script path populates the
    # arrays itself (so the sentinel gets set below without backfilling); the
    # Agent path leaves them empty on first save, so the hook backfills + sets
    # the sentinel; any later operator clear is then respected.
    already = bool(data.get("_placementsBackfilled"))
    has_products = bool(data.get("products") or [])
    has_links = bool(data.get("links") or [])
    if not already and not has_products and derived_products:
        data["products"] = derived_products
    if not already and not has_links and derived_links:
        data["links"] = derived_links
    # Set the sentinel once placements exist (populated by script/Agent/hook).
    if (data.get("products") or []) or (data.get("links") or []):
        data["_placementsBackfilled"] = True

    if bad_urls:
        warnings.append({
            "kind": "inline_link",
            "problem": "invented_or_malformed_povison_url",
            "urls": bad_urls[:10],
        })
    return warnings


@app.post("/api/tasks")
async def create_task(body: dict | None = None) -> dict:
    """Create a task (3 steps) or fork from a parent at fork_step."""
    b = body or {}
    task = _db.create_task(
        label=b.get("label"),
        parent_task_id=b.get("parent_task_id"),
        fork_step=b.get("fork_step"),
    )
    # Also create a shadow run dir for script temp IO (scripts still need -o paths)
    try:
        d = RUNS_DIR / task["task_id"]
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return {"ok": True, "task": task}


@app.post("/api/tasks/import")
async def import_tasks(body: dict | None = None) -> dict:
    """Import legacy run directories from ``SEO_RUNS_DIR`` into tasks/steps."""
    b = body or {}
    if b.get("run_id"):
        d = RUNS_DIR / str(b["run_id"])
        if not d.is_dir():
            raise HTTPException(status_code=404, detail=f"run dir not found: {d}")
        row = _db.import_run_from_disk(
            d,
            label=b.get("label"),
            skip_existing=not bool(b.get("force")),
        )
        return {"ok": True, **row}
    result = _db.import_runs_from_disk(
        RUNS_DIR,
        skip_existing=not bool(b.get("force")),
        limit=int(b.get("limit") or 100),
    )
    return result


@app.post("/api/tasks/{task_id}/audit")
async def record_task_audit(task_id: str, body: dict | None = None) -> dict:
    """Record an operator action in the task audit log (e.g. confirm placements)."""
    _task_or_404(task_id)
    b = body or {}
    _db.record_audit(
        task_id,
        action=str(b.get("action") or ""),
        detail=str(b.get("detail") or ""),
        status=str(b.get("status") or "ok"),
        step_num=b.get("step_num"),
    )
    return {"ok": True}


@app.post("/api/tasks/reset")
async def reset_tasks(body: dict | None = None) -> dict:
    """Wipe all tasks; keep the keyword pool by default (largest step-1 set)."""
    b = body or {}
    return _db.reset_all_tasks(keep_keywords=not bool(b.get("drop_keywords")))


@app.get("/api/tasks")
async def list_tasks(limit: int = 50) -> dict:
    return {"ok": True, "tasks": _db.list_tasks(limit), "stats": _db.stats()}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    return {"ok": True, "task": _task_or_404(task_id)}


@app.post("/api/tasks/{task_id}/activate")
async def activate_task(task_id: str) -> dict:
    """Operator opened this task — completed → idle."""
    task = _db.activate_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return {"ok": True, "task": task}


@app.get("/api/tasks/{task_id}/steps/{step_num}")
async def get_step(task_id: str, step_num: int) -> dict:
    _task_or_404(task_id)
    step = _db.get_step(task_id, step_num)
    if not step:
        raise HTTPException(status_code=404, detail="step not found")
    return {"ok": True, "step": step}


@app.get("/api/tasks/{task_id}/steps/{step_num}/data")
async def get_step_data(task_id: str, step_num: int):
    _task_or_404(task_id)
    data = _db.get_step_data(task_id, step_num)
    if data is None:
        raise HTTPException(status_code=404, detail="no data for this step yet")
    return data


@app.put("/api/tasks/{task_id}/steps/{step_num}/data")
async def put_step_data(task_id: str, step_num: int, request: Request) -> dict:
    _task_or_404(task_id)
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8") or "null")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e
    status = (request.query_params.get("status") or "done").strip()
    # Article-state field protection: an intermediate Agent save (e.g. during
    # the placements sub-step) can briefly carry an empty `sections`/`faqs`/...
    # field. Backfill those from the DB so a later sub-step never wipes an
    # earlier sub-step's already-generated output. In editorial mode `products`
    # (the 3 review cards) is also backfilled; `links` stays untouched (empty
    # is meaningful there).
    backfilled = 0
    if step_num == 3 and isinstance(data, dict):
        existing = _db.get_step_data(task_id, step_num)
        backfilled = _backfill_empty_content_fields(data, existing)
    # Root-cause fix for 404 placements: validate product/link URLs on save.
    # If any URL looks fabricated, block placementsConfirmed and surface warnings.
    placement_warnings: list[dict] = []
    if step_num == 3 and isinstance(data, dict):
        placement_warnings = _validate_placement_urls(data)
        # Merged-flow post-save hook (C1): derive placements from inline links in
        # prose, backfill empty products/links, flag invented povison URLs (C2).
        inline_warnings = _post_save_step3_inline_placements(data)
        placement_warnings.extend(inline_warnings)
        if placement_warnings:
            data["placementWarnings"] = placement_warnings
            # Force the gate closed so the operator must fix URLs before proceeding.
            if data.get("placementsConfirmed"):
                data["placementsConfirmed"] = False
                if isinstance(data.get("phaseDone"), dict):
                    data["phaseDone"]["placements"] = False
        else:
            data.pop("placementWarnings", None)
    step = _db.save_step_data(task_id, step_num, data, status=status)
    # Mirror to run dir for script compatibility
    try:
        d = RUNS_DIR / task_id
        d.mkdir(parents=True, exist_ok=True)
        name = {1: "kw.json", 2: "topics.json", 3: "article-state.json"}.get(step_num)
        if name:
            (d / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    resp = {"ok": True, "step": step}
    if placement_warnings:
        resp["placementWarnings"] = placement_warnings
    return resp


@app.post("/api/tasks/{task_id}/steps/{step_num}/run")
async def run_step(task_id: str, step_num: int, body: dict | None = None) -> dict:
    """Run the deterministic script for a step; ingest result into DB."""
    _task_or_404(task_id)
    b = body or {}
    d = RUNS_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    _db.set_step_status(task_id, step_num, "running")
    _db.mark_task_running(task_id)

    if step_num == 1:
        mode = b.get("mode", "discover")  # discover | enrich
        if mode == "enrich":
            raw = d / "kw.raw.json"
            # Prefer DB step-1 data if file missing
            if not raw.exists():
                prev = _db.get_step_data(task_id, 1)
                if prev:
                    (d / "kw.raw.json").write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
                    raw = d / "kw.raw.json"
            if not raw.exists():
                _db.set_step_status(task_id, 1, "error")
                raise HTTPException(status_code=400, detail="no keywords to enrich — run discover first")
            cmd = [_py(), str(SCRIPTS / "enrich-keyword-metrics.py"), "-i", str(raw), "-o", str(d / "kw.json")]
            if b.get("seed_demo"):
                cmd.insert(-2, "--seed-demo")

            def _on_done():
                try:
                    kw = json.loads((d / "kw.json").read_text(encoding="utf-8"))
                    _db.save_step_data(task_id, 1, kw if isinstance(kw, list) else kw.get("keywords") or [], status="done")
                except Exception as e:
                    _db.set_step_status(task_id, 1, "error")
                    _db.record_audit(task_id, "enrich_error", str(e), "error", 1)

            job_id = _spawn_job("enrich-keyword-metrics", cmd, d, task_id, _on_done)
            return {"job_id": job_id, "task_id": task_id, "step_num": 1}

        raw_sources = b.get("sources", "brand media")
        sources = [str(s) for s in raw_sources] if isinstance(raw_sources, list) else str(raw_sources).replace(",", " ").split()
        if not sources:
            sources = ["brand", "media"]
        cmd = [
            _py(), str(SCRIPTS / "keyword-discovery.py"),
            "--sources", *sources,
            "--min-freq", str(b.get("min_freq", 2)),
            "-o", str(d / "kw.raw.json"),
        ]

        def _on_disc():
            try:
                kw = json.loads((d / "kw.raw.json").read_text(encoding="utf-8"))
                _db.save_step_data(task_id, 1, kw if isinstance(kw, list) else [], status="done")
            except Exception as e:
                _db.set_step_status(task_id, 1, "error")
                _db.record_audit(task_id, "discover_error", str(e), "error", 1)

        job_id = _spawn_job("keyword-discovery", cmd, d, task_id, _on_disc)
        return {"job_id": job_id, "task_id": task_id, "step_num": 1}

    if step_num == 2:
        # Ensure kw.json from step 1 DB
        kw_data = _db.get_step_data(task_id, 1)
        if kw_data:
            (d / "kw.json").write_text(json.dumps(kw_data, ensure_ascii=False, indent=2), encoding="utf-8")
        kw_path = d / "kw.json"
        if not kw_path.exists() and (d / "kw.raw.json").exists():
            kw_path = d / "kw.raw.json"
        if not kw_path.exists():
            _db.set_step_status(task_id, 2, "error")
            raise HTTPException(status_code=400, detail="step 1 keywords missing")
        cmd = [_py(), str(SCRIPTS / "topic-brainstorm.py"), "-i", str(kw_path), "-n", str(b.get("n", 10))]
        if b.get("demo"):
            cmd.append("--demo")
        # optional category anchors
        cats = b.get("categories")
        if cats:
            cmd += ["--categories", ",".join(str(c) for c in cats)]
        # optional keyword filter
        selected = b.get("keywords")
        if selected:
            filtered = [k for k in (kw_data or []) if isinstance(k, dict) and k.get("text") in selected]
            if filtered:
                filt_path = d / "kw.selected.json"
                filt_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
                # replace -i arg
                cmd[cmd.index("-i") + 1] = str(filt_path)
        cmd += ["-o", str(d / "topics.json")]

        def _on_bs():
            try:
                doc = json.loads((d / "topics.json").read_text(encoding="utf-8"))
                _db.save_step_data(task_id, 2, doc, status="done")
            except Exception as e:
                _db.set_step_status(task_id, 2, "error")
                _db.record_audit(task_id, "brainstorm_error", str(e), "error", 2)

        job_id = _spawn_job("topic-brainstorm", cmd, d, task_id, _on_bs)
        return {"job_id": job_id, "task_id": task_id, "step_num": 2}

    if step_num == 3:
        # Materialize topics + state for section-generate
        topics = _db.get_step_data(task_id, 2)
        if topics:
            (d / "topics.json").write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
        state = _db.get_step_data(task_id, 3)
        state_path = d / "article-state.json"
        if state:
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        topic_path = d / "topics.json"
        rules_arg = SKILL_DIR / "data" / "generation-rules.json"
        cmd = [
            _py(), str(SCRIPTS / "section-generate.py"),
            "--mode", str(b.get("mode", "all")),
            "--topic-id", str(b.get("topic_id", "t01")),
            "--rules", str(rules_arg),
            "--catalog", str(SKILL_DIR / "data" / "placement-catalog.json"),
            "-o", str(state_path),
        ]
        if b.get("demo"):
            cmd.append("--demo")
        if state_path.exists():
            cmd += ["--state", str(state_path)]
        elif topic_path.exists():
            cmd += ["--topic", str(topic_path)]
        else:
            _db.set_step_status(task_id, 3, "error")
            raise HTTPException(status_code=400, detail="step 2 topics or step 3 state required")
        if b.get("section_id"):
            cmd += ["--section-id", str(b["section_id"])]

        def _on_sec():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
                # Route through the same step-3 post-save hook (C1) so this script
                # path also gets inline-link sanitization + products/links backfill.
                if isinstance(st, dict):
                    inline_warnings = _post_save_step3_inline_placements(st)
                    if inline_warnings:
                        st["placementWarnings"] = st.get("placementWarnings", []) + inline_warnings
                        if st.get("placementsConfirmed"):
                            st["placementsConfirmed"] = False
                            if isinstance(st.get("phaseDone"), dict):
                                st["phaseDone"]["placements"] = False
                    state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                _db.save_step_data(task_id, 3, st, status="done")
            except Exception as e:
                _db.set_step_status(task_id, 3, "error")
                _db.record_audit(task_id, "section_error", str(e), "error", 3)

        job_id = _spawn_job("section-generate", cmd, d, task_id, _on_sec)
        return {"job_id": job_id, "task_id": task_id, "step_num": 3}

    raise HTTPException(status_code=400, detail=f"invalid step_num: {step_num}")


@app.post("/api/tasks/{task_id}/steps/{step_num}/agent")
async def task_agent_run(task_id: str, step_num: int, body: dict | None = None) -> dict:
    """Launch Hermes Agent for a step; instruct it to save via seo_save_step_data."""
    _task_or_404(task_id)
    b = body or {}
    if not GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="HERMES_GATEWAY_KEY unset — start gateway first")
    step = b.get("step") or ({1: "keywords", 2: "brainstorm", 3: "section"}.get(step_num, "section"))
    keywords = b.get("keywords") or []
    categories = b.get("categories") or []
    n_topics = int(b.get("n") or 10)
    db_path = str(_db._db_path())

    _db.clear_progress(task_id, step_num)
    _db.set_step_status(task_id, step_num, "running")
    _db.mark_task_running(task_id)

    # Materialize prior steps for agent context (read-only files under task dir)
    d = RUNS_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    for n, name in ((1, "kw.json"), (2, "topics.json"), (3, "article-state.json")):
        data = _db.get_step_data(task_id, n)
        if data is not None:
            (d / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    substep_guidance = {
        "serp": (
            "Analyze SERP for the topic: fetch top-10 results, group them into clusters by angle/intent, "
            "and identify content gaps. The articleState.serp object MUST use this schema: "
            "{ranks:[{cluster:'<short label>', results:[{title,url,domain,angle}]}], gaps:['<gap text>', ...]}. "
            "Each rank entry needs a human-readable cluster label and a results array with title/url/domain/angle. "
            "Set articleState.phaseDone.serp = true."
        ),
        "outline": (
            "Generate a blog outline based on the topic and any SERP analysis. "
            "Set articleState.outline = [{id, level:'h2'|'h3', text:'...'}, ...] and "
            "articleState.phaseDone.outline = true. Do NOT set outlineConfirmed."
        ),
        "section": _section_guidance_for_task(d),
        "faq": (
            "Generate FAQ (4-6 Q&A). Update articleState.faqs and set articleState.phaseDone.faq = true."
        ),
        "meta": (
            "Generate SEO meta (title/description/slug). Update articleState.meta and "
            "set articleState.phaseDone.meta = true.\n"
            "HARD LENGTH LIMITS (write to fit, NEVER over-write then truncate):\n"
            "- title: 50–60 characters (English). This is the META title for SERP, NOT the "
            "article H1. When the H1 is longer than 60 chars, REWRITE a shorter click-worthy "
            "meta title — do NOT copy the H1 verbatim and do NOT truncate with '...'.\n"
            "- description: 150–160 characters. Same rule — rewrite to fit, do not truncate. "
            "Vary the opening (avoid starting every article with Discover/Learn/Find out).\n"
            "- slug: lowercase, hyphenated, ≤75 characters, aligned with the rewritten meta "
            "title (not a verbatim slug of the full H1).\n"
            "- focus: primary keyword phrase.\n"
            "Before saving, count each field's characters and confirm title is in [50,60] and "
            "description is in [150,160]. If out of range, revise the wording until it fits."
        ),
        "placements": _PLACEMENTS_SUBSTEP_GUIDANCE,
    }
    step_guidance = {
        1: "Discover / enrich keywords. When done call seo_save_step_data(task_id, step_num=1, data=<keyword array>).",
        2: (
            f"SERP-driven topic brainstorm for {n_topics} candidates. "
            f"品类关键词 (anchor, merge all enabled): {', '.join(str(c) for c in categories) or '(none — 不限定品类)'}. "
            f"联想关键词 (random 3-8 combined per topic): {', '.join(str(k) for k in keywords) or '(see kw.json)'}. "
            "Build each topic AROUND the category keywords (when 不限定品类 only, no category constraint), "
            "weaving in 3-8 of the associative keywords. "
            "Write topics envelope per topic-brainstorm-schema v1.0. "
            f"When done call seo_save_step_data(task_id='{task_id}', step_num=2, data=<topics envelope>). "
            "Do NOT enter Step 3."
        ),
        3: (
            f"Step={step}. {substep_guidance.get(step, 'Update the article state for this sub-step.')}\n"
            f"Read the current articleState from the DB step 3 data (context file article-state.json), "
            f"modify ONLY the relevant fields for this sub-step, then call "
            f"seo_save_step_data(task_id='{task_id}', step_num=3, data=<full updated articleState>) "
            f"so the operator UI can render it. Always save the COMPLETE articleState object."
        ),
    }.get(step_num, f"Step {step_num}.")

    prompt = (
        f"Use skill povison-seo-blog.\n"
        f"TASK_ID: {task_id}\n"
        f"STEP_NUM: {step_num}\n"
        f"DB path (for tools): {db_path}\n"
        f"Context files (read-only helpers) in: {d.resolve()}\n"
        f"{step_guidance}\n"
        f"LIVE PROGRESS: call seo_report_progress with task_id='{task_id}', step_num={step_num}, "
        f"step, task, conclusion, status at EVERY sub-step.\n"
        f"Do NOT use write_file for final outputs — use seo_save_step_data.\n"
        f"execute_code is DISABLED for this run — use the terminal tool for shell commands.\n"
        f"Topic: {b.get('topic_title', '')}\n"
    )
    if step_num == 2:
        prompt += f"Keywords JSON: {json.dumps(keywords, ensure_ascii=False)}\n"
        prompt += f"Categories JSON: {json.dumps(categories, ensure_ascii=False)}\n"

    payload = {
        "input": b.get("prompt") or prompt,
        "instructions": "You are operating the povison-seo-blog skill for the SEO Studio operator.",
        "session_id": f"seo-studio:{task_id}",
        "yolo": True,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as cx:
            resp = await cx.post(
                f"{GATEWAY_BASE}/v1/runs",
                json=payload,
                headers={"Authorization": f"Bearer {GATEWAY_KEY}", "Content-Type": "application/json"},
            )
        if resp.status_code not in (200, 202):
            _db.set_step_status(task_id, step_num, "error")
            raise HTTPException(status_code=502, detail=f"gateway {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        gw_id = data.get("run_id", "")
        _db.set_step_status(task_id, step_num, "running", agent_run_id=gw_id)
        _db.record_audit(task_id, f"agent:{step}", f"gateway_run={gw_id}", "running", step_num)
        (d / ".agent_run_id").write_text(gw_id, encoding="utf-8")
        return {"ok": True, "gateway": data, "run_id": gw_id, "task_id": task_id, "step_num": step_num}
    except httpx.HTTPError as e:
        _db.set_step_status(task_id, step_num, "error")
        raise HTTPException(status_code=503, detail=f"gateway unreachable: {e}") from e


@app.get("/api/tasks/{task_id}/steps/{step_num}/agent/status")
async def task_agent_status(task_id: str, step_num: int) -> dict:
    _task_or_404(task_id)
    step = _db.get_step(task_id, step_num)
    run_id = (step or {}).get("agent_run_id") or ""
    if not run_id:
        marker = RUNS_DIR / task_id / ".agent_run_id"
        run_id = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if not run_id or not GATEWAY_KEY:
        return {"ok": False, "run_id": run_id, "detail": "no agent run or no gateway key"}
    try:
        async with httpx.AsyncClient(timeout=20) as cx:
            resp = await cx.get(
                f"{GATEWAY_BASE}/v1/runs/{run_id}",
                headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            )
        if resp.status_code != 200:
            return {"ok": False, "run_id": run_id, "status_code": resp.status_code}
        gw = resp.json()
        st = gw.get("status")
        if st in ("completed", "succeeded"):
            # If agent already saved via tool, step may be done; else leave running for UI disk fallback
            cur = _db.get_step(task_id, step_num)
            if cur and cur.get("status") == "running" and cur.get("data") is not None:
                _db.set_step_status(task_id, step_num, "done")
            elif cur and cur.get("status") == "running" and step_num == 3:
                _db.mark_task_completed_if_ready(task_id)
        elif st in ("failed", "error"):
            _db.set_step_status(task_id, step_num, "error")
        return {"ok": True, "run_id": run_id, "gateway": gw, "task": _db.get_task(task_id)}
    except httpx.HTTPError as e:
        return {"ok": False, "run_id": run_id, "detail": str(e)}


@app.get("/api/tasks/{task_id}/steps/{step_num}/agent/progress")
async def task_agent_progress(task_id: str, step_num: int, since: int = 0) -> dict:
    """Read-only progress from DB (never mutates)."""
    _task_or_404(task_id)
    return _db.list_progress(task_id, step_num, since)


@app.get("/api/tasks/{task_id}/active-script-job")
async def task_active_script_job(task_id: str) -> dict:
    """Return the in-flight script job (if any) for a task, so the client can
    resume polling it after a task switch. Script-based generation (FAQ/Meta/
    placements re-resolve via ``bridgeGenerateSection``) writes a ``.script_job``
    marker in the run dir with the job_id; this reads it and looks up the live
    job status. Returns ``{job_id: null}`` when no script job is in flight."""
    _task_or_404(task_id)
    marker = RUNS_DIR / task_id / ".script_job"
    job_id = None
    mode = None
    raw = None
    if marker.exists():
        try:
            raw = marker.read_text(encoding="utf-8").strip()
        except Exception:
            raw = None
    if raw:
        # Marker is either JSON {"job_id":..., "mode":...} (section-generate) or a
        # plain job_id string (keyword discover/enrich, brainstorm).
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                job_id = parsed.get("job_id")
                mode = parsed.get("mode")
            else:
                job_id = str(parsed)
        except (json.JSONDecodeError, ValueError):
            job_id = raw
    status = None
    kind = None
    if job_id:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                status = job.get("status")
                kind = job.get("kind")
    # If the marker exists but the job is no longer in _JOBS (server restarted
    # mid-job) OR the job already finished, treat it as not-in-flight so the
    # client doesn't poll a dead/finished id forever. Clean up a stale marker.
    if status is None or status in ("succeeded", "failed"):
        if status is None and job_id:
            try:
                marker.unlink()
            except Exception:
                pass
        job_id = None
        mode = None
    return {"job_id": job_id, "status": status, "kind": kind, "mode": mode}


# ---- WordPress draft export --------------------------------------------------
@app.get("/api/wordpress/health")
async def wordpress_health() -> dict:
    """Report WP connection config + REST/auth status (no secrets)."""
    try:
        return _wp.healthcheck()
    except Exception as e:
        return {"configured": False, "rest_api": "error", "auth": "error", "error": str(e)}


# ---- Stock images (Pixabay / Openverse) --------------------------------------
@app.get("/api/stock-images/health")
async def stock_images_health() -> dict:
    """Report Pixabay key + Openverse availability (no secrets)."""
    try:
        import stock_images as _stock

        return _stock.config_snapshot()
    except Exception as e:
        return {"configured": False, "error": str(e)}


@app.post("/api/stock-images/search")
async def stock_images_search(body: dict | None = None) -> dict:
    """Search Pixabay/Openverse for body-image candidates.

    Body: ``{query, source?: auto|pixabay|openverse, per_page?: 1-10}``.
    Legacy ``unsplash`` / ``pexels`` source values are treated as ``auto``.
    Returns a candidate pool the agent/operator must pick from — never invent URLs.
    """
    try:
        import stock_images as _stock
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"stock_images module missing: {e}") from e
    b = body or {}
    query = str(b.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    result = _stock.search_stock_images(
        query,
        source=str(b.get("source") or "auto"),
        per_page=int(b.get("per_page") or 5),
    )
    if not result.get("ok"):
        # 200 with ok=false so the agent can read the error and skip the section
        return result
    return result


# ---- POVISON product catalog (keyword search + detail + recommend) -----------
@app.get("/api/povison-products/health")
async def povison_products_health() -> dict:
    """Report POVISON catalog API reachability (no secrets; storeId=3 only)."""
    try:
        import povison_catalog as _cat
        return _cat.health()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/povison-products/search")
async def povison_products_search(body: dict | None = None) -> dict:
    """Search POVISON catalog by keyword. Returns candidates with image + tags.

    Body: ``{keyword, limit?: 1-30}``.
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    keyword = str(b.get("keyword") or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    return _cat.search_products(keyword, page=1, page_size=int(b.get("limit") or 15))


@app.post("/api/povison-products/lookup")
async def povison_products_lookup(body: dict | None = None) -> dict:
    """Look up a single product by URL/path + optional variant/sku.

    Body: ``{url, sku?, variant?}``. Returns name, url, image, specs, dimensions.
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    url = str(b.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    return _cat.lookup_detail(url, variant=b.get("variant"))


@app.post("/api/povison-products/recommend")
async def povison_products_recommend(body: dict | None = None) -> dict:
    """Recommend 1-2 products for an article based on topic + sections.

    Body: ``{topic: {...}, sections: [...]}``. Returns ``products[]`` ready to
    write into ``articleState.products`` (status=pending, with image).
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    topic = b.get("topic") or {}
    sections = b.get("sections") or []
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    return _cat.recommend_placements(topic, sections, limit=int(b.get("limit") or 2))


@app.post("/api/povison-products/scrape")
async def povison_products_scrape(body: dict | None = None) -> dict:
    """Fallback PDP scrape (JSON-LD Product). For link verification / Detail fail.

    Body: ``{url}``.
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    url = str(b.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    return _cat.scrape_pdp(url)


@app.post("/api/povison-products/enrich-image")
async def povison_products_enrich_image(body: dict | None = None) -> dict:
    """Enrich a single product's image by looking up its PDP via Detail API.

    Body: ``{url}``. Returns ``{ok, image, name?}`` — never raises.
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    url = str(b.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}
    detail = _cat.lookup_detail(url)
    if detail.get("ok"):
        return {"ok": True, "image": detail.get("image") or "", "name": detail.get("name") or ""}
    # Fallback to scrape.
    sc = _cat.scrape_pdp(url)
    if sc.get("ok"):
        return {"ok": True, "image": sc.get("image") or "", "name": sc.get("name") or ""}
    return {"ok": False, "error": detail.get("error") or "enrich failed"}


@app.post("/api/povison-products/enrich-batch")
async def povison_products_enrich_batch(body: dict | None = None) -> dict:
    """Best-effort image enrichment for products missing image but having url.

    Body: ``{products: [{id?, url, name?}, ...]}``. Only enriches items where
    ``image`` is empty and ``url`` is present. Returns updated products list.
    """
    try:
        import povison_catalog as _cat
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_catalog module missing: {e}") from e
    b = body or {}
    products = b.get("products") or []
    if not isinstance(products, list):
        raise HTTPException(status_code=400, detail="products must be a list")
    out = []
    for p in products:
        if not isinstance(p, dict):
            continue
        if p.get("image") or not p.get("url"):
            out.append(p)
            continue
        res = await povison_products_enrich_image({"url": p["url"]})
        if res.get("ok") and res.get("image"):
            enriched = dict(p)
            enriched["image"] = res["image"]
            if not enriched.get("name") and res.get("name"):
                enriched["name"] = res["name"]
            out.append(enriched)
        else:
            out.append(p)
    return {"ok": True, "products": out}


# ---- One-click parse-card: URL → full product card (lookup + reviews + LLM blurb) ----

def _flatten_dimensions(dims: dict) -> str:
    """Flatten ``lookup_detail``'s ``dimensions`` dict into a single string.

    Prefers an ``overall`` key if the API provides it; otherwise joins
    width/depth/height/weight in that order, then any remaining keys.
    """
    if not isinstance(dims, dict) or not dims:
        return ""
    overall = dims.get("overall") or dims.get("Overall")
    if overall:
        return str(overall).strip()
    preferred = ("width", "depth", "height", "weight")
    parts: list[str] = []
    seen: set[str] = set()
    for k in preferred:
        if k in dims and dims[k]:
            parts.append(str(dims[k]))
            seen.add(k)
    for k, v in dims.items():
        if k not in seen and v:
            parts.append(f"{k}: {v}")
    return " × ".join(parts) if len(parts) <= 3 else "; ".join(parts)


def _infer_mechanism(detail: dict) -> str:
    """Best-effort ``mechanism`` string from assembly/style/drawers.

    The Detail API has no ``mechanism`` field — we infer from what's
    available so the editorial card's ``specs.mechanism`` is at least
    partially populated. Product-specific phrases (e.g. "reversible
    chaise") are left for the LLM blurb step to surface from the product
    name + specs context.
    """
    raw = detail.get("specs") or {}
    parts: list[str] = []
    assembly = detail.get("assembly") or raw.get("assembly_required")
    if assembly:
        a = str(assembly).strip()
        if a.lower() in ("no", "false", "0"):
            parts.append("no assembly required")
        else:
            parts.append(f"assembly: {a}")
    if raw.get("style"):
        parts.append(str(raw["style"]))
    if raw.get("number_of_drawers"):
        parts.append(f"{raw['number_of_drawers']} drawers")
    return ", ".join(parts)


def _map_detail_to_specs(detail: dict) -> dict:
    """Map ``lookup_detail`` → editorial card ``specs`` shape.

    Editorial cards expect ``specs`` as a dict of strings:
    ``{dimensions, mechanism, material, colors}``. ``lookup_detail``
    returns a flat ``specs`` dict (``assembly_required``, ``material``,
    ``color``…) and a separate top-level ``dimensions`` dict. This bridges
    the mismatch.
    """
    raw = detail.get("specs") or {}
    specs: dict[str, str] = {}
    dim_str = _flatten_dimensions(detail.get("dimensions") or {})
    if dim_str:
        specs["dimensions"] = dim_str
    if raw.get("material"):
        specs["material"] = str(raw["material"])
    if raw.get("color"):
        # singular API key → plural editorial convention
        specs["colors"] = str(raw["color"])
    mechanism = _infer_mechanism(detail)
    if mechanism:
        specs["mechanism"] = mechanism
    return specs


# Reasoning-model / chatty-model leak markers seen in parse-card blurbs
# (chain-of-thought written into message.content instead of the final copy).
_BLURB_COT_MARKERS = (
    "the user wants",
    "let me analyze",
    "let me write",
    "let me draft",
    "let me count",
    "let me trim",
    "word count",
    "paragraph 1",
    "paragraph 2",
    "i'll skip",
    "i will write",
    "here's my draft",
    "here is my draft",
)


def _sanitize_parse_card_blurb(raw: str | None, *, style: str) -> tuple[str | None, str]:
    """Accept only a final product blurb; reject chain-of-thought dumps.

    Returns ``(blurb_or_none, reason)`` where reason is ``ok``, ``empty``,
    ``cot_leak``, or ``too_long``. Editorial blurbs must stay within ~90-150
    words (hard cap 180); inline within ~40-70 (hard cap 100). Over-length or
    CoT-looking text is discarded so it never lands in article preview.
    """
    text = (raw or "").strip()
    if not text:
        return None, "empty"
    low = text.lower()
    if any(m in low for m in _BLURB_COT_MARKERS):
        return None, "cot_leak"
    words = len(text.split())
    max_words = 180 if style == "editorial" else 100
    if words > max_words:
        return None, "too_long"
    return text, "ok"


@app.post("/api/povison-products/parse-card")
async def povison_products_parse_card(body: dict | None = None) -> dict:
    """One-click parse a PDP URL into a full product card.

    Body: ``{url, style?, topic?}`` where ``style`` is ``"inline"`` (40-70w
    blurb) or ``"editorial"`` (90-150w blurb with specs paragraph). Chains:
      1. ``povison_catalog.lookup_detail(url)`` → name, image, specs, price
      2. ``povison_reviews.resolve_spu_by_url`` + ``fetch_reviews(limit=1)``
         → reviewQuote
      3. ``llm_client.chat()`` → blurb (from specs + review context)

    Each sub-step is independent (graceful degradation): a failed lookup,
    review fetch, or LLM call returns ``null`` for that field rather than
    failing the whole endpoint. Returns ``{ok, name, url, image, specs,
    price, review_count, reviewQuote, blurb}``.

    ``specs`` and ``reviewQuote`` are only populated in editorial mode
    (inline mode ignores them — the UI doesn't render those fields).
    """
    b = body or {}
    url = str(b.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    style = str(b.get("style") or "inline").strip()
    is_editorial = style == "editorial"
    topic = b.get("topic") or {}

    # Step 1: Detail API lookup (name, image, specs, dimensions, price).
    try:
        import povison_catalog as _cat
    except ImportError:
        raise HTTPException(status_code=503, detail="povison_catalog module missing")
    try:
        detail = _cat.lookup_detail(url)
    except Exception as e:
        return {"ok": False, "error": f"lookup failed: {e}", "url": url}
    if not detail.get("ok"):
        return {"ok": False, "error": detail.get("error", "product not found"), "url": url}

    name = detail.get("name") or ""
    image = detail.get("image") or ""
    price = detail.get("price")
    review_count = detail.get("review_count")
    specs = _map_detail_to_specs(detail) if is_editorial else None

    # Step 2: Buyer review (editorial only — inline cards don't render reviewQuote).
    review_quote: dict | None = None
    if is_editorial:
        try:
            import povison_reviews as _rv
            spu = _rv.resolve_spu_by_url(url)
            if spu:
                reviews = _rv.fetch_reviews(spu=spu, limit=1, min_rating=4)
                if reviews:
                    r = reviews[0]
                    review_quote = {
                        "reviewer": r.get("nickname") or "",
                        "date": r.get("date") or "",
                        "quote": r.get("detail") or "",
                        "rating": r.get("rating"),
                    }
        except Exception as e:
            pass  # review_quote stays None — never fabricate

    # Step 3: LLM blurb generation from specs + review context.
    blurb: str | None = None
    try:
        import sys as _sys
        if str(SCRIPTS) not in _sys.path:
            _sys.path.insert(0, str(SCRIPTS))
        from llm_client import chat as _chat

        if is_editorial:
            blurb_instr = (
                "Write a 90-150 word editorial blurb in 1-3 paragraphs. "
                "MUST include a specs paragraph covering dimensions, "
                "mechanism, material, and colors. Optionally add a scene "
                "paragraph and a buyer-review quote paragraph."
            )
            max_tok = 500
        else:
            blurb_instr = (
                "Write a 40-70 word inline product blurb. Link the product "
                "name to the PDP."
            )
            max_tok = 250

        system_prompt = (
            "You are a POVISON product copywriter. "
            f"{blurb_instr} "
            "Use the product specs and optional buyer review below as source. "
            "CRITICAL: Output ONLY the final blurb prose that will be published. "
            "Do NOT explain your plan, analyze the brief, count words, draft "
            "alternatives, or narrate your reasoning. "
            "Plain text only — no JSON, no markdown headings, no preamble, "
            "no postamble."
        )
        user_payload = json.dumps(
            {
                "name": name,
                "url": url,
                "specs": specs or (detail.get("specs") or {}),
                "dimensions": detail.get("dimensions") or {},
                "price": price,
                "reviewQuote": review_quote,
                "topic": topic,
            },
            ensure_ascii=False,
        )
        raw = _chat(
            system_prompt,
            user_payload,
            max_tokens=max_tok,
            temperature=0.4,
            timeout=60,
        )
        blurb, _reason = _sanitize_parse_card_blurb(raw, style=style)
    except Exception:
        pass  # blurb stays None — UI keeps existing value

    result = {
        "ok": True,
        "name": name,
        "url": detail.get("url") or url,
        "image": image,
        "specs": specs,
        "price": price,
        "review_count": review_count,
        "reviewQuote": review_quote,
        "blurb": blurb,
    }
    return result


# ---- POVISON blog internal-link catalog (sitemap-based; no secrets) ----

@app.get("/api/povison-blog/health")
async def povison_blog_health() -> dict:
    """Report POVISON blog sitemap reachability + cached article count."""
    try:
        import povison_blog as _blog
        return _blog.health()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/povison-blog/search")
async def povison_blog_search(body: dict | None = None) -> dict:
    """Search povison.com/blog articles by keyword (matches URL slug).

    Body: ``{keyword, limit?: 1-50}``. Returns ranked candidates with
    ``url``, ``slug``, ``title_guess``, ``category``, ``score``, ``reasons``.
    """
    try:
        import povison_blog as _blog
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_blog module missing: {e}") from e
    b = body or {}
    keyword = str(b.get("keyword") or "").strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    return _blog.search_articles(keyword, limit=int(b.get("limit") or 10))


@app.post("/api/povison-blog/recommend-links")
async def povison_blog_recommend_links(body: dict | None = None) -> dict:
    """Recommend 2-3 internal links for an article based on topic + sections.

    Body: ``{topic: {primary_keyword, secondary_keywords, category_keywords},
    sections: [...], existing_urls?: [...], limit?: 1-5}``. Returns
    ``links[]`` ready to append to ``articleState.links`` (status=pending).
    All URLs are real povison.com/blog/ articles from the sitemap — never
    fabricated.
    """
    try:
        import povison_blog as _blog
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_blog module missing: {e}") from e
    b = body or {}
    topic = b.get("topic") or {}
    sections = b.get("sections") or []
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    return _blog.recommend_links(
        topic,
        sections,
        existing_urls=b.get("existing_urls"),
        limit=int(b.get("limit") or 3),
    )


@app.post("/api/povison-blog/verify")
async def povison_blog_verify(body: dict | None = None) -> dict:
    """Verify a URL is a real povison.com/blog/ article present in the sitemap.

    Body: ``{url}``. Returns ``{ok, verified, url, article?}``. Never raises.
    """
    try:
        import povison_blog as _blog
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_blog module missing: {e}") from e
    b = body or {}
    url = str(b.get("url") or "").strip()
    if not url:
        return {"ok": False, "verified": False, "error": "url is required"}
    return _blog.verify_url(url)


# ── POVISON product reviews (magento2 DB) — backs Editorial Picks ────────────
#
# Read-only surface for the Editorial Picks placement style: each editorial
# product card can quote one real APPROVED buyer review (name + date + quote +
# star rating). The DB connection is configured via MAGENTO_DB_* env (profile
# .env). When not configured, endpoints return ok=False (not 500) so the UI can
# degrade gracefully and the Agent prompt knows to skip review enrichment.


@app.get("/api/povison-reviews/health")
async def povison_reviews_health() -> dict:
    """Report whether the magento2 review DB is reachable + configured."""
    try:
        import povison_reviews as _rv
        return {"ok": _rv.is_configured(), "configured": _rv.is_configured()}
    except Exception as e:
        return {"ok": False, "configured": False, "error": str(e)}


@app.get("/api/povison-reviews/by-spu")
async def povison_reviews_by_spu(spu: str, limit: int = 5, min_rating: int = 0) -> dict:
    """Fetch APPROVED reviews for a product SPU, best-rated first.

    Query params: ``spu`` (required), ``limit`` 1-50 (default 5), ``min_rating``
    0-5 star floor (default 0 = no filter). Returns ``{ok, spu, count,
    reviews[]}`` where each review has ``reviewId, nickname, date, title,
    detail, rating(1-5), helpfulCount, sourceType``. Returns ``ok=False`` (not
    500) when the DB is not configured — callers (Agent / UI) treat that as
    "no reviews available" and skip review enrichment.
    """
    try:
        import povison_reviews as _rv
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_reviews module missing: {e}") from e
    if not spu:
        raise HTTPException(status_code=400, detail="spu is required")
    reviews = _rv.fetch_reviews(spu=spu, limit=int(limit), min_rating=int(min_rating))
    return {"ok": bool(reviews), "spu": spu, "count": len(reviews), "reviews": reviews}


@app.get("/api/povison-reviews/summary")
async def povison_reviews_summary(spu: str) -> dict:
    """Aggregate review count + average rating for a SPU (from the pre-computed
    ``review_entity_summary`` table). Returns ``{ok, spu, reviewsCount,
    ratingSummary, rating}``. Never raises."""
    try:
        import povison_reviews as _rv
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_reviews module missing: {e}") from e
    if not spu:
        raise HTTPException(status_code=400, detail="spu is required")
    out = _rv.fetch_summary(spu=spu)
    return {"ok": out["reviewsCount"] > 0, "spu": spu, **out}


@app.get("/api/povison-reviews/by-url")
async def povison_reviews_by_url(url: str, limit: int = 1, min_rating: int = 4) -> dict:
    """Resolve a povison product URL to its magento SPU and fetch APPROVED reviews.

    This is the entry point the Editorial Picks Agent should use: it accepts the
    product's storefront URL (the only stable id on the editorial card) and
    internally resolves the numeric magento ``entity_id`` via the ``url_key``
    attribute, then returns the best-rated review. Use this instead of
    ``by-spu`` when you only have the product URL.

    Query params: ``url`` (required, povison product URL), ``limit`` 1-50
    (default 1 — one good quote per card), ``min_rating`` 0-5 star floor
    (default 4). Returns ``{ok, url, spu, count, reviews[]}`` with the same
    review shape as ``by-spu``. ``ok=False`` when not configured, URL not
    resolvable, or no matching reviews — callers OMIT reviewQuote in that case.
    """
    try:
        import povison_reviews as _rv
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"povison_reviews module missing: {e}") from e
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    spu = _rv.resolve_spu_by_url(url)
    if not spu:
        return {"ok": False, "url": url, "spu": None, "count": 0, "reviews": []}
    reviews = _rv.fetch_reviews(spu=spu, limit=int(limit), min_rating=int(min_rating))
    return {"ok": bool(reviews), "url": url, "spu": spu, "count": len(reviews), "reviews": reviews}


# ---- Placement URL liveness guard (HTTP reachability) ----

@app.get("/api/povison-placements/health")
async def povison_placements_guard_health() -> dict:
    """Report placement-guard reachability (probes one povison URL)."""
    try:
        import placement_guard as _guard
        return _guard.health()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/povison-placements/verify-urls")
async def povison_placements_verify_urls(body: dict | None = None) -> dict:
    """Live HTTP liveness check for a batch of placement URLs.

    Body: ``{urls: ["..."]}``. Probes each URL (HEAD→GET, follow redirects,
    10s timeout, povison host whitelist). Returns
    ``{ok, checked_at, total, dead_count, results: [{url, live, status_code, final_url, error}]}``.
    Use this as the second guard rail after the pattern validator — catches
    real-shaped but dead (404) URLs that the pattern check misses.
    """
    try:
        import placement_guard as _guard
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"placement_guard module missing: {e}") from e
    b = body or {}
    urls = b.get("urls") or []
    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail="urls must be a list")
    return _guard.check_urls(urls, workers=int(b.get("workers") or 6))


@app.post("/api/tasks/{task_id}/steps/3/verify-placements")
async def verify_task_placements(task_id: str) -> dict:
    """Verify all product + link URLs in a task's step-3 articleState are live.

    Loads the task's step-3 data, collects every ``products[].url`` and
    ``links[].url`` (plus ``products[].image`` if you want — skipped by default
    since images are static.povison.com and rarely 404), runs the liveness
    guard in parallel, writes the result into
    ``articleState.placementUrlCheck`` so the UI can render it, and returns
    the same. The UI's 「确认，写 FAQ」 button stays disabled while any URL is
    dead.
    """
    _task_or_404(task_id)
    try:
        import placement_guard as _guard
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"placement_guard module missing: {e}") from e
    data = _db.get_step_data(task_id, 3) or {}
    urls: list[str] = []
    url_kinds: dict[str, str] = {}  # url -> "product"/"link"
    for p in data.get("products") or []:
        if isinstance(p, dict):
            u = (p.get("url") or "").strip()
            if u:
                urls.append(u)
                url_kinds[u] = "product"
    for l in data.get("links") or []:
        if isinstance(l, dict):
            u = (l.get("url") or "").strip()
            if u:
                urls.append(u)
                url_kinds[u] = "link"
    check = _guard.check_urls(urls, workers=6)
    # Annotate each result with kind
    for r in check.get("results") or []:
        r["kind"] = url_kinds.get(r.get("url") or "", "unknown")
    # Persist into articleState so the UI gate can read it
    data["placementUrlCheck"] = {
        "checked_at": check.get("checked_at"),
        "total": check.get("total"),
        "dead_count": check.get("dead_count"),
        "results": check.get("results"),
    }
    # If any URL is dead, force the confirm gate closed
    if check.get("dead_count"):
        data["placementsConfirmed"] = False
        if isinstance(data.get("phaseDone"), dict):
            data["phaseDone"]["placements"] = False
    _db.save_step_data(task_id, 3, data, status="done")
    return {"ok": True, **check}


@app.post("/api/tasks/{task_id}/wordpress/draft")
async def wordpress_draft(task_id: str, body: dict | None = None) -> dict:
    """Export the task's article to WordPress as a draft.

    Builds the full blog-template HTML from the DB step-3 articleState, then
    delegates to the operator's ``wordpress_mcp`` publisher (same pipeline the
    agent uses via MCP). The parser extracts ``<article class="article-body">``
    as post content and reads title/slug/meta-description/FAQ-schema from the
    head. By default images are sideloaded into the WP Media Library so the
    first becomes the featured image — pass ``skip_image_upload: true`` to keep
    original URLs instead.
    """
    _task_or_404(task_id)
    b = body or {}
    state = _db.get_step_data(task_id, 3)
    if not state or not (state.get("topic") or (state.get("meta") or {}).get("title")):
        raise HTTPException(status_code=400, detail="no article to export — generate content first")
    # Best-effort: enrich missing product images from PDP Detail API before export.
    try:
        products = state.get("products") or []
        need = [p for p in products if isinstance(p, dict) and not p.get("image") and p.get("url") and p.get("status") != "rejected"]
        if need:
            import povison_catalog as _cat
            for p in need:
                detail = _cat.lookup_detail(p["url"])
                if detail.get("ok") and detail.get("image"):
                    p["image"] = detail["image"]
                    if not p.get("name") and detail.get("name"):
                        p["name"] = detail["name"]
    except Exception:
        pass  # enrichment is best-effort; never block export
    html = fill_blog_template(state)
    # Also refresh the run-dir preview.html so the on-disk copy matches what was pushed.
    try:
        (RUNS_DIR / task_id / "preview.html").write_text(html, encoding="utf-8")
    except OSError:
        pass
    try:
        result = _wp.publish_draft(
            html_content=html,
            category_id=b.get("category_id"),
            tag_ids=b.get("tag_ids"),
            focus_keyword=(state.get("meta") or {}).get("focus"),
            skip_image_upload=bool(b.get("skip_image_upload", False)),
            status=b.get("status", "draft"),
        )
    except Exception as e:
        _db.record_audit(task_id, "wp_draft", "export", "failed", 3, error=str(e)[:300])
        raise HTTPException(status_code=502, detail=f"WordPress export failed: {e}") from e
    _db.record_audit(
        task_id, "wp_draft", f"post_id={result.get('post_id')}", "ok", 3,
        error=str(result.get("edit_url", "")),
    )
    return {"ok": True, "task_id": task_id, **result}


# ---- runs --------------------------------------------------------------------
@app.post("/api/runs")
async def create_run(body: dict | None = None) -> dict:
    label = (body or {}).get("label")
    d = _new_run_dir(label)
    rid = _run_id(d)
    try:
        _db.record_run(rid, str(d), label)
    except Exception:
        pass
    return {"id": rid, "path": str(d)}


@app.get("/api/runs/{rid}/file/{name}")
async def get_file(rid: str, name: str):
    d = _resolve_run(rid)
    p = d / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {name}")
    text = p.read_text(encoding="utf-8")
    if name.endswith(".json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return PlainTextResponse(text)
    if name.endswith(".html"):
        return PlainTextResponse(text)
    return PlainTextResponse(text)


@app.put("/api/runs/{rid}/file/{name}")
async def put_file(rid: str, name: str, request: Request):
    d = _resolve_run(rid)
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    # Read the raw body ourselves — FastAPI's `body: Any = None` silently converts
    # an empty JSON array `[]` to Python None (a known gotcha), which would corrupt
    # .json files. Parsing the raw bytes preserves `[]` as a real list.
    raw_bytes = await request.body()
    raw = raw_bytes.decode("utf-8") if raw_bytes else ""
    body: Any = None
    if raw.strip():
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
    if isinstance(body, (dict, list)):
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif isinstance(body, str):
        p.write_text(body, encoding="utf-8")
    elif body is None:
        # Never write the literal "None" — it corrupts .json files (not valid JSON).
        p.write_text("null\n", encoding="utf-8")
    else:
        p.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    # Record structured artifacts into the DB for queryable history.
    # For DB-backed task-* ids, the DB step data is the source of truth and is
    # managed via PUT /steps/{n}/data (UI edits) and seo_save_step_data (agent).
    # A file PUT here is just a shadow copy for the agent/ scripts — it must NOT
    # overwrite the DB, otherwise a stale in-memory articleState (e.g. missing an
    # outline the agent already saved) clobbers the authoritative DB state.
    try:
        is_task = str(rid).startswith("task-")
        if name == "kw.json" and isinstance(body, list):
            if not is_task:
                _db.record_keywords(rid, body)
        elif name == "topics.json" and isinstance(body, dict):
            if not is_task:
                _db.record_topics(rid, body.get("topics") or [])
        elif name == "article-state.json" and isinstance(body, dict):
            if not is_task:
                _db.record_article_state(rid, body)
        elif name == "generation-rules.json" and isinstance(body, dict):
            _db.record_generation_rules(rid, body)
        _db.record_audit(rid, "file", "put", name, "ok")
    except Exception:
        pass
    return {"ok": True, "name": name}


# ---- jobs --------------------------------------------------------------------
@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)


# ---- script wrappers ---------------------------------------------------------
@app.post("/api/runs/{rid}/keywords/discover")
async def keywords_discover(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    # sources may arrive as "brand media" (string) or ["brand","media"] (list);
    # argparse nargs='+' needs separate argv elements.
    raw_sources = b.get("sources", "brand media")
    if isinstance(raw_sources, list):
        sources = [str(s) for s in raw_sources]
    else:
        sources = str(raw_sources).replace(",", " ").split()
    if not sources:
        sources = ["brand", "media"]
    cmd = [
        _py(), str(SCRIPTS / "keyword-discovery.py"),
        "--sources", *sources,
        "--min-freq", str(b.get("min_freq", 2)),
        "-o", str(d / "kw.raw.json"),
    ]
    _is_task, _on_disc_finish = _script_step_track(rid, 1, mark_done_on_success=True)
    job_id = _spawn_job("keyword-discovery", cmd, d, rid, None, _on_disc_finish)
    if _is_task:
        _write_script_job_marker(rid, job_id)
    return {"job_id": job_id}


@app.post("/api/runs/{rid}/keywords/enrich")
async def keywords_enrich(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    raw = d / "kw.raw.json"
    if not raw.exists():
        raise HTTPException(status_code=400, detail="kw.raw.json missing — run discover first")
    cmd = [_py(), str(SCRIPTS / "enrich-keyword-metrics.py"), "-i", str(raw)]
    if b.get("seed_demo"):
        cmd.append("--seed-demo")
    if b.get("emit_batches"):
        cmd.append("--emit-batches")
    cmd += ["-o", str(d / "kw.json")]
    def _on_enrich_done():
        try:
            kw = json.loads((d / "kw.json").read_text(encoding="utf-8"))
            _db.record_keywords(rid, kw if isinstance(kw, list) else kw.get("keywords") or [])
        except Exception:
            pass
    _is_task, _on_enrich_finish = _script_step_track(rid, 1)
    job_id = _spawn_job("enrich-keyword-metrics", cmd, d, rid, _on_enrich_done, _on_enrich_finish)
    if _is_task:
        _write_script_job_marker(rid, job_id)
    return {"job_id": job_id}


@app.post("/api/runs/{rid}/topics/brainstorm")
async def topics_brainstorm(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    kw_path = d / "kw.json"
    # For DB-backed tasks, hydrate the file shadow from the DB (source of truth)
    # so the script reads authoritative keywords, not the stale in-memory copy
    # the UI just wrote via bridgeSaveFile. Without this, a re-brainstorm would
    # read stale keywords and the _on_bs_done callback would overwrite the DB
    # step-2 topics with a result derived from stale input.
    if str(rid).startswith("task-"):
        try:
            prev_kw = _db.get_step_data(rid, 1)
            if prev_kw:
                kw_path.write_text(json.dumps(prev_kw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    # If kw.json is missing, corrupted, JSON null, or empty, fall back to kw.raw.json
    # so brainstorm still works after a discover that wasn't enriched (or when the
    # browser saved an empty KEYWORDS list that FastAPI collapsed to null).
    raw = d / "kw.raw.json"
    use_raw = False
    if not kw_path.exists():
        use_raw = True
    else:
        try:
            parsed = json.loads(kw_path.read_text(encoding="utf-8"))
            if parsed is None or (isinstance(parsed, list) and not parsed):
                use_raw = True
        except (json.JSONDecodeError, ValueError):
            use_raw = True
    if use_raw and raw.exists():
        kw_path = raw
    cmd = [_py(), str(SCRIPTS / "topic-brainstorm.py"), "-i", str(kw_path), "-n", str(b.get("n", 10))]
    if b.get("demo"):
        cmd.append("--demo")
    cmd += ["-o", str(d / "topics.json")]
    def _on_bs_done():
        try:
            doc = json.loads((d / "topics.json").read_text(encoding="utf-8"))
            _db.record_topics(rid, doc.get("topics") or doc if isinstance(doc, list) else doc.get("topics") or [])
        except Exception:
            pass
    _is_task, _on_bs_finish = _script_step_track(rid, 2)
    job_id = _spawn_job("topic-brainstorm", cmd, d, rid, _on_bs_done, _on_bs_finish)
    if _is_task:
        _write_script_job_marker(rid, job_id)
    return {"job_id": job_id}


@app.post("/api/runs/{rid}/sections/generate")
async def sections_generate(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    topic_path = d / "topics.json"
    state_path = d / "article-state.json"
    # For DB-backed tasks, hydrate the file shadow from the DB (source of truth)
    # so the script reads the authoritative articleState (with agent-saved
    # outline/serp/sections), not the stale in-memory copy the UI just wrote via
    # bridgeSaveFile. Without this, _on_sec_done would overwrite the DB step-3
    # data with a state derived from stale input, clobbering agent-saved fields.
    if str(rid).startswith("task-"):
        try:
            db_state = _db.get_step_data(rid, 3)
            if db_state:
                state_path.write_text(json.dumps(db_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            db_topics = _db.get_step_data(rid, 2)
            if db_topics:
                topic_path.write_text(json.dumps(db_topics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    rules_path = d / "generation-rules.json"
    # Use run-dir rules only if valid JSON; else fall back to canonical default.
    rules_arg = SKILL_DIR / "data" / "generation-rules.json"
    if rules_path.exists():
        try:
            json.loads(rules_path.read_text(encoding="utf-8"))
            rules_arg = rules_path
        except json.JSONDecodeError:
            pass
    cmd = [
        _py(), str(SCRIPTS / "section-generate.py"),
        "--mode", str(b.get("mode", "all")),
        "--topic-id", str(b.get("topic_id", "t01")),
        "--rules", str(rules_arg),
        "--catalog", str(SKILL_DIR / "data" / "placement-catalog.json"),
    ]
    if b.get("demo"):
        cmd.append("--demo")
    if state_path.exists():
        cmd += ["--state", str(state_path)]
    else:
        if not topic_path.exists():
            raise HTTPException(status_code=400, detail="topics.json or article-state.json required")
        cmd += ["--topic", str(topic_path)]
    if b.get("section_id"):
        cmd += ["--section-id", str(b["section_id"])]
    cmd += ["-o", str(state_path)]
    is_task = str(rid).startswith("task-")
    # Mark step 3 (and the task) as running in the DB so the task list shows
    # "运行中" during script-based generation (FAQ/Meta/placements re-resolve).
    # The agent path does this in task_agent_run; the script path previously did
    # NOT, so the task list showed idle and resumeTaskAgentPolling could not
    # restore the progress bar after a task switch.
    if is_task:
        try:
            _db.set_step_status(rid, 3, "running", clear_agent_run_id=True)
            _db.mark_task_running(rid)
        except Exception:
            pass
    def _on_sec_done():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            # Route through the same step-3 post-save hook (C1) so the script path
            # also gets inline-link validation + products/links backfill from prose.
            if isinstance(st, dict):
                inline_warnings = _post_save_step3_inline_placements(st)
                if inline_warnings:
                    st["placementWarnings"] = st.get("placementWarnings", []) + inline_warnings
                    if st.get("placementsConfirmed"):
                        st["placementsConfirmed"] = False
                        if isinstance(st.get("phaseDone"), dict):
                            st["phaseDone"]["placements"] = False
                # Persist the backfilled state back to the run file + DB.
                state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _db.record_article_state(rid, st)
        except Exception:
            pass
    def _on_sec_finish(job_id, final_status, rc):
        # Runs in finally (success OR failure). record_article_state already
        # marked step 3 "done" on success; on failure mark "error" so the step
        # is not stuck "running". Always clear the script-job marker and let the
        # task settle (completed if step 3 done, else idle so it is not stuck).
        if is_task:
            try:
                marker = d / ".script_job"
                if marker.exists():
                    marker.unlink()
            except Exception:
                pass
            try:
                if final_status != "succeeded":
                    _db.set_step_status(rid, 3, "error")
                _db.mark_task_completed_if_ready(rid)
                # If the task is still "running" after the above (e.g. step 3
                # errored), flip it to idle so the operator can retry and the
                # task list does not show a phantom "运行中".
                if final_status != "succeeded":
                    _db.set_task_status(rid, "idle")
            except Exception:
                pass
    job_id = _spawn_job("section-generate", cmd, d, rid, _on_sec_done, _on_sec_finish)
    # Persist the job_id + mode in a run-dir marker so the client can resume
    # polling this script job after a task switch AND land on the right phase
    # (faq/meta/sections) instead of auto-advancing past it.
    if is_task:
        try:
            (d / ".script_job").write_text(
                json.dumps({"job_id": job_id, "mode": str(b.get("mode") or "section")}),
                encoding="utf-8",
            )
        except Exception:
            pass
    return {"job_id": job_id}


@app.post("/api/runs/{rid}/validate")
async def validate(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    state_path = d / "article-state.json"
    # For DB-backed tasks, hydrate the file shadow from the DB (source of truth)
    # so validate-article.py reads the authoritative articleState (with agent-
    # saved outline/serp/sections), not the stale in-memory copy the UI just
    # wrote. Without this, the record_article_state call below would overwrite
    # the DB step-3 data with a state derived from stale input.
    if str(rid).startswith("task-"):
        try:
            db_state = _db.get_step_data(rid, 3)
            if db_state:
                state_path.write_text(json.dumps(db_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    if not state_path.exists():
        raise HTTPException(status_code=400, detail="article-state.json missing")
    rc, out, err = _run_sync([_py(), str(SCRIPTS / "validate-article.py"), "-i", str(state_path), "--update-state"], d)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    v = state.get("validation") or {}
    try:
        _db.record_article_state(rid, state)
        _db.record_audit(rid, "validate", "validate-article", f"{v.get('passed')}/{v.get('total')}", "ok" if rc in (0, 2) else "failed", rc, err[:300])
    except Exception:
        pass
    return {"ok": rc in (0, 2), "validation": v}


def _run_sync(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=SCRIPT_TIMEOUT)
    return proc.returncode, proc.stdout, proc.stderr


# ---- preview assembly (port of Studio fillBlogTemplate) ----------------------
@app.post("/api/runs/{rid}/preview")
async def assemble_preview(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    state_path = d / "article-state.json"
    if not state_path.exists():
        raise HTTPException(status_code=400, detail="article-state.json missing")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    html = fill_blog_template(state)
    out = d / "preview.html"
    out.write_text(html, encoding="utf-8")
    return {"ok": True, "path": str(out), "bytes": len(html)}


_PEXELS_HEROES = [
    "https://images.pexels.com/photos/1571460/pexels-photo-1571460.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/1648776/pexels-photo-1648776.jpeg?auto=compress&cs=tinysrgb&w=1200",
    "https://images.pexels.com/photos/276583/pexels-photo-276583.jpeg?auto=compress&cs=tinysrgb&w=1200",
]


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _strip_content_markers(content: str) -> str:
    text = content or ""
    text = re.sub(r"\[Product:\s*[^\]]+\]", "", text)
    text = re.sub(r"\[Internal link:[^\]]+\]", "", text)
    return text


def _strip_legacy_placements(content: str) -> str:
    """Remove trailing auto-applied placement blocks (marker + blurb paragraph).

    Older UI wrote `\n\n[Product: name]\n<blurb>` and `\n\n[Internal link: ...]`
    directly into section content. These markers always sat at the end of a
    section, so a greedy match from the first trailing placement marker to
    end-of-string safely removes the whole appended block (any mix of product
    and internal-link blocks). Keeps any operator-edited prose before the
    first marker intact.
    """
    text = content or ""
    text = re.sub(r"\n\n\[(Product|Internal link):[\s\S]*$", "", text)
    return text


def _strip_orphaned_placement_blurbs(content: str, state: dict, sec: dict) -> str:
    """Remove bare product blurbs left by the old buggy "写入正文" button.

    The old clear regex only deleted `[Product: name]` markers, so repeated
    clicks stacked the blurb paragraph without markers. `_strip_legacy_placements`
    cannot see those orphans. Remove every occurrence of each accepted product's
    blurb (plain and markdown-linked name variants) for this section so
    `_inject_products_md` can re-append exactly once.
    """
    text = content or ""
    sid = sec.get("id")
    if not sid:
        return text
    for p in state.get("products") or []:
        if not isinstance(p, dict) or p.get("status") != "accepted":
            continue
        if (p.get("sectionId") or p.get("section")) != sid:
            continue
        blurb = (p.get("blurb") or "").strip()
        name = (p.get("name") or "").strip()
        url = (p.get("url") or "").strip()
        if not blurb:
            continue
        variants = [blurb]
        if name and url:
            variants.append(blurb.replace(name, f"[{name}]({url})", 1))
        for v in variants:
            if not v:
                continue
            while v in text:
                text = text.replace(v, "")
    # Leftover standalone markers (not only trailing)
    text = re.sub(r"\n\n\[Product:\s*[^\]]+\]", "", text)
    text = re.sub(r"\n\n\[Internal link:[^\]]*\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _strip_rejected_links_from_prose(content: str, state: dict, sec: dict) -> str:
    """Remove/unwrap inline placements the operator REJECTED for this section.

    Merged-flow sections bake accepted placements inline as markdown links. When
    an operator rejects a card, the corresponding inline link must not ship to
    WordPress:

    - Rejected *internal links*: unwrap ``[anchor](url)`` → ``anchor`` (keep the
      words, drop the link).
    - Rejected *products*: the LLM wrote a short standalone blurb paragraph
      containing ``[Product Name](url)``. Remove that whole paragraph. If the
      product link also appears elsewhere (woven into a content paragraph), just
      unwrap it there rather than dropping real content. Guard: only drop a
      paragraph when it is blurb-sized (≤ ~100 words) AND contains the rejected
      product URL — protects against nuking a long content paragraph.
    """
    text = content or ""
    sid = sec.get("id")
    if not sid:
        return text
    rejected_p_urls: set[str] = set()
    rejected_l_urls: set[str] = set()
    for p in state.get("products") or []:
        if isinstance(p, dict) and p.get("status") == "rejected" and (p.get("sectionId") or p.get("section")) == sid:
            if p.get("url"):
                rejected_p_urls.add(p["url"])
    for l in state.get("links") or []:
        if isinstance(l, dict) and l.get("status") == "rejected" and (l.get("sectionId") or l.get("section")) == sid:
            if l.get("url"):
                rejected_l_urls.add(l["url"])
    if not rejected_p_urls and not rejected_l_urls:
        return text

    # Products: drop blurb-sized paragraphs containing the rejected product link.
    if rejected_p_urls:
        paras = re.split(r"(\n\n+)", text)
        kept: list[str] = []
        for para in paras:
            if re.fullmatch(r"\n\n+", para):
                kept.append(para)
                continue
            urls_in = {u for _, u in _MD_LINK_RE.findall(para)}
            if urls_in & rejected_p_urls:
                word_count = len(para.split())
                if word_count <= 100:
                    # standalone blurb paragraph — drop it
                    continue
                # long content paragraph — keep, but unwrap the link below
            kept.append(para)
        text = "".join(kept)

    # Unwrap any remaining rejected product links + all rejected internal links.
    def _unwrap(m: re.Match) -> str:
        anchor, url = m.group(1), m.group(2)
        if url in rejected_l_urls or url in rejected_p_urls:
            return anchor
        return m.group(0)

    text = _MD_LINK_RE.sub(_unwrap, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _prepare_section_content(state: dict, sec: dict) -> str:
    """Prepare a section's content for final article assembly.

    Two paths:

    - **Merged flow** (new tasks): the section LLM already wove accepted
      placements inline as markdown links with real povison URLs. We only strip
      the placements the operator REJECTED (unwrap links, drop rejected product
      blurb paragraphs). No ``Related:`` fallback, no trailing blurb append —
      the prose already contains everything.

    - **Legacy flow** (old tasks): section content has no inline povison links;
      placements live separately in ``state[products]/[links]``. We inline-link
      accepted internal links into the first plain-text anchor occurrence
      (falling back to a trailing ``Related:`` line when the anchor is absent),
      strip legacy ``[Product: ...]`` markers + orphan blurbs, then append
      accepted product blurbs once at the end.
    """
    raw = sec.get("content") or ""
    # Editorial Picks dispatch (plan §V): in editorial mode the body sections
    # must NOT carry inline povison links (the product cards live in the
    # standalone POVISON Picks H2, rendered by _editorial_picks_html). Force the
    # legacy assembly path so that even if the Agent accidentally wrote a
    # povison citation into a section, it is NOT treated as a merged-flow
    # placement (which would strip/reject it). The merged-flow path is reserved
    # for inline-mode articles.
    if (state.get("placementStyle") or "inline") == "editorial":
        return _prepare_section_content_legacy(state, sec, raw)
    if _has_inline_povison_links(raw):
        # Merged flow: links are already inline. Strip rejected ones, then append
        # blurbs for accepted products that are NOT inline in the prose (e.g.
        # manually-added product cards) so their blurb copy still ships (#4).
        cleaned = _strip_rejected_links_from_prose(raw, state, sec)
        inline_urls = {u for _, u in _MD_LINK_RE.findall(cleaned)}
        extra_blurbs: list[str] = []
        for p in state.get("products") or []:
            if not isinstance(p, dict):
                continue
            if (p.get("sectionId") or p.get("section")) != sec.get("id"):
                continue
            if p.get("status") != "accepted":
                continue
            url = (p.get("url") or "").strip()
            if url and url in inline_urls:
                continue  # already inline in prose — no trailing blurb needed
            blurb = (p.get("blurb") or "").strip()
            name = (p.get("name") or "").strip()
            if not blurb and not name:
                continue
            text = blurb or name
            if name and url:
                text = text.replace(name, f"[{name}]({url})", 1)
            extra_blurbs.append(text)
        if extra_blurbs:
            return cleaned.rstrip() + "\n\n" + "\n\n".join(extra_blurbs)
        return cleaned

    # Legacy flow
    return _prepare_section_content_legacy(state, sec, raw)


def _prepare_section_content_legacy(state: dict, sec: dict, raw: str) -> str:
    """Legacy assembly path: strip legacy markers/orphan blurbs, weave accepted
    internal links inline into prose (first occurrence, ``Related:`` fallback),
    then append accepted product blurbs once at the end.

    Used directly in editorial mode (placements live in the POVISON Picks H2,
    not the body) and as the legacy fallback for inline-mode sections that have
    no inline povison links.
    """
    sec_content = _strip_legacy_placements(raw)
    sec_content = _strip_orphaned_placement_blurbs(sec_content, state, sec)
    # Inline-link accepted internal links into body prose (first occurrence).
    fallback_links: list[str] = []
    for l in state.get("links") or []:
        if not isinstance(l, dict):
            continue
        if (l.get("sectionId") or l.get("section")) != sec.get("id"):
            continue
        if l.get("status") != "accepted":
            continue
        anchor = (l.get("anchor") or "").strip()
        url = (l.get("url") or "").strip()
        if not anchor or not url:
            continue
        sec_content, replaced = _inline_link_replace(sec_content, anchor, url)
        if not replaced:
            fallback_links.append(f"Related: [{anchor}]({url})")
    # Append accepted product blurbs (single source of truth: articleState.products).
    product_md = _inject_products_md(state, sec)
    parts: list[str] = []
    if sec_content.strip():
        parts.append(sec_content.rstrip())
    if product_md:
        parts.append(product_md)
    if fallback_links:
        parts.append("\n\n".join(fallback_links))
    return "\n\n".join(parts)


def _inline_link_replace(content: str, anchor: str, url: str) -> tuple[str, bool]:
    """Replace first plain-text occurrence of ``anchor`` with ``[anchor](url)``.

    Case-insensitive match, word-bounded, preserves the body's original casing
    of the matched text (so ``Sintered Stone Dining Table`` in body stays capitalized
    when wrapped). Skips occurrences already inside a markdown link ``[...](...)``
    to avoid nesting. Returns ``(new_content, replaced?)``.
    """
    if not content or not anchor or not url:
        return content, False
    try:
        pattern = re.compile(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", re.I)
    except re.error:
        return content, False
    # Spans of existing links (markdown [...](...) and HTML <a ...>...</a>) — skip
    # matches inside them to avoid nesting a link inside another link.
    link_spans = [m.span() for m in re.finditer(r"\[[^\]]*\]\([^)]*\)|<a\b[^>]*>.*?</a>", content, re.I | re.S)]
    for m in pattern.finditer(content):
        s, e = m.span()
        if any(ls <= s < le or ls < e <= le for ls, le in link_spans):
            continue
        matched_text = content[s:e]
        replacement = f"[{matched_text}]({url})"
        return content[:s] + replacement + content[e:], True
    return content, False


def _inject_products_md(state: dict, sec: dict) -> str:
    """Build markdown for accepted product blurbs attached to ``sec``.

    Single source of truth is ``articleState.products`` (status='accepted',
    sectionId matches). Each product contributes its blurb with the product
    name hyperlinked to the PDP. Returns empty string when nothing applies.
    """
    sid = sec.get("id")
    if not sid:
        return ""
    parts: list[str] = []
    for p in state.get("products") or []:
        if not isinstance(p, dict):
            continue
        if (p.get("sectionId") or p.get("section")) != sid:
            continue
        if p.get("status") != "accepted":
            continue
        blurb = (p.get("blurb") or "").strip()
        name = (p.get("name") or "").strip()
        url = (p.get("url") or "").strip()
        if not blurb and not name:
            continue
        text = blurb or name
        if name and url:
            text = text.replace(name, f"[{name}]({url})", 1)
        parts.append(text)
    return "\n\n".join(parts)


def _is_md_table_sep(line: str) -> bool:
    """True for GFM separator rows like ``| --- | :---: |``."""
    s = line.strip()
    if "|" not in s:
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c)


def _is_md_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _md_table_to_html(rows: list[str]) -> str:
    """Convert a GFM pipe-table block into an HTML <table>."""
    if len(rows) < 2:
        return ""
    header_cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body_rows = rows[2:] if _is_md_table_sep(rows[1]) else rows[1:]
    html = (
        '<table style="width:100%;border-collapse:collapse;margin:24px 0;'
        "font-size:15px;font-family:'Helvetica Neue',Arial,sans-serif;\">"
    )
    html += "<thead><tr>"
    for cell in header_cells:
        html += (
            f'<th style="background:#f5efe6;padding:10px 14px;text-align:left;'
            f'font-weight:700;border:1px solid #e8e2d8;">{_esc(cell)}</th>'
        )
    html += "</tr></thead><tbody>"
    for i, row in enumerate(body_rows):
        if not _is_md_table_row(row):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        bg = "background:#fbf9f5;" if i % 2 == 1 else ""
        html += "<tr>"
        for cell in cells:
            # Allow simple markdown links inside cells
            cell_html = re.sub(
                r"\[([^\]]+)\]\((https?://[^)]+)\)",
                r'<a href="\2">\1</a>',
                _esc(cell),
            )
            html += (
                f'<td style="padding:10px 14px;border:1px solid #e8e2d8;'
                f'vertical-align:top;{bg}">{cell_html}</td>'
            )
        html += "</tr>"
    html += "</tbody></table>"
    return html


def _section_html(content: str) -> list[str]:
    """Convert section markdown to HTML chunks.

    Prefer the ``markdown`` library (GFM tables, lists, links). Fall back to a
    line-oriented parser that still converts pipe tables — without this,
    WordPress shows raw ``| col | col |`` text because every line was wrapped
    in ``<p>``.
    """
    text = _strip_content_markers(content).strip()
    if not text:
        return []

    try:
        import markdown as md  # type: ignore

        html = md.markdown(
            text,
            extensions=["tables", "nl2br", "sane_lists", "fenced_code"],
        )
        # Add inline table styles so WP (which drops <head> CSS) still looks OK
        html = html.replace(
            "<table>",
            '<table style="width:100%;border-collapse:collapse;margin:24px 0;'
            "font-size:15px;font-family:'Helvetica Neue',Arial,sans-serif;\">",
        )
        html = html.replace(
            "<th>",
            '<th style="background:#f5efe6;padding:10px 14px;text-align:left;'
            'font-weight:700;border:1px solid #e8e2d8;">',
        )
        html = html.replace(
            "<td>",
            '<td style="padding:10px 14px;border:1px solid #e8e2d8;vertical-align:top;">',
        )
        # Markdown ![](url) becomes bare <img> — wrap so WP themes center them
        html = _center_imgs_in_html(html)
        return [html]
    except ImportError:
        pass

    # Fallback: line parser with GFM table detection
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_md_table_row(line) and i + 1 < len(lines) and _is_md_table_sep(lines[i + 1]):
            block = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and _is_md_table_row(lines[i]):
                block.append(lines[i])
                i += 1
            table_html = _md_table_to_html(block)
            if table_html:
                out.append(table_html)
            continue
        t = _esc(line)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
        t = t.strip()
        if not t:
            i += 1
            continue
        if t.startswith("•") or t.startswith("·") or t.startswith("- "):
            out.append(f"<li>{t.lstrip('•·- ').strip()}</li>")
        else:
            out.append(f"<p>{t}</p>")
        i += 1
    return out


def _slugify_heading(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60]


def _chunks_to_html(chunks: list[str]) -> str:
    body = ""
    in_list = False
    for c in chunks:
        if c.startswith("<li>"):
            if not in_list:
                body += "<ul>"
                in_list = True
            body += c
        else:
            if in_list:
                body += "</ul>"
                in_list = False
            body += c
    if in_list:
        body += "</ul>"
    return body


def _centered_image_html(
    *,
    url: str,
    alt: str = "",
    caption: str = "",
    credit: str = "",
    max_width: int = 680,
    extra_wrap_style: str = "",
    link_url: str = "",
) -> str:
    """Build image HTML that stays centered across most WP themes.

    Many themes override ``figure`` / ``.aligncenter`` (float, width:100%,
    ``img { display:inline }``). The reliable pattern is:

    1. Outer wrapper with ``text-align:center`` + ``!important`` margins
    2. ``figure`` as ``display:table; margin-left/right:auto`` (shrink-wraps + centers)
    3. ``img.aligncenter`` with ``display:block !important; margin:auto !important``
    4. Caption under the image, also ``text-align:center``

    When ``link_url`` is set (product PDP), both the image and caption wrap in
    ``<a href>`` — matching published POVISON blog product figures.
    """
    if not url:
        return ""
    href = (link_url or "").strip()
    if href and not re.match(r"^https?://", href, re.I):
        href = ""

    cap_inner = _esc(caption) if caption else ""
    if credit and not href:
        # Stock-photo credit only when not a linked product figure.
        cap_inner += (
            f' <span style="color:#9b9b9b;font-size:12px;margin-left:6px;">{_esc(credit)}</span>'
        )
    if href and cap_inner:
        cap_inner = (
            f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:inherit;text-decoration:underline;">{cap_inner}</a>'
        )
    cap_html = ""
    if cap_inner:
        cap_html = (
            f'<figcaption class="wp-element-caption" style="display:block;text-align:center !important;'
            f"margin:12px auto 0;font-size:15px;color:#444;line-height:1.55;"
            f"font-family:'Helvetica Neue',Arial,sans-serif;\">"
            f"{cap_inner}</figcaption>"
        )

    img_tag = (
        f'<img class="aligncenter size-full" src="{_esc(url)}" alt="{_esc(alt)}" '
        f'loading="lazy" width="{max_width}" '
        'style="display:block !important;margin-left:auto !important;margin-right:auto !important;'
        f"max-width:100%;height:auto;width:auto;border-radius:6px;\">"
    )
    if href:
        img_tag = (
            f'<a href="{_esc(href)}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-block;">{img_tag}</a>'
        )

    wrap_style = (
        f"text-align:center !important;margin:28px auto !important;max-width:{max_width}px;"
        f"width:100%;{extra_wrap_style}"
    )
    return (
        f'<div class="wp-block-image aligncenter" style="{wrap_style}">'
        '<figure class="aligncenter size-full" style="display:table !important;margin:0 auto !important;'
        'text-align:center !important;max-width:100%;">'
        f"{img_tag}{cap_html}</figure></div>"
    )


def _center_imgs_in_html(html: str) -> str:
    """Wrap bare ``<img>`` tags (e.g. from markdown) so they center in WP themes."""
    if not html or "<img" not in html.lower():
        return html

    def _from_img_tag(tag: str) -> str:
        if "aligncenter" in tag and "margin-left:auto" in tag.replace(" ", ""):
            return tag
        src_m = re.search(r'\bsrc=["\']([^"\']+)["\']', tag, re.I)
        alt_m = re.search(r'\balt=["\']([^"\']*)["\']', tag, re.I)
        if not src_m:
            return tag
        return _centered_image_html(
            url=src_m.group(1),
            alt=alt_m.group(1) if alt_m else "",
        )

    # Prefer replacing <p><img></p> so we don't nest <div> inside <p>
    html = re.sub(
        r"<p>\s*(<img\b[^>]*>)\s*</p>",
        lambda m: _from_img_tag(m.group(1)),
        html,
        flags=re.I,
    )
    # Remaining bare <img> not already inside a figure/div we built
    def _wrap_bare(match: re.Match) -> str:
        tag = match.group(0)
        # Skip if already has our centering styles
        if "aligncenter" in tag and "margin-left:auto" in tag.replace(" ", ""):
            return tag
        return _from_img_tag(tag)

    html = re.sub(r"<img\b[^>]*>", _wrap_bare, html, flags=re.I)
    return html


def _inline_images_html(sec: dict) -> str:
    """Render section images centered for WordPress themes."""
    imgs = sec.get("images") or []
    if not isinstance(imgs, list) or not imgs:
        return ""
    out = []
    for im in imgs:
        if not isinstance(im, dict) or not im.get("url"):
            continue
        out.append(
            _centered_image_html(
                url=im["url"],
                alt=im.get("alt") or "",
                caption=im.get("caption") or "",
                credit=im.get("credit") or "",
                max_width=680,
            )
        )
    return "".join(out)


def _product_caption(name: str) -> str:
    """Caption text for a product figure — prefer a Povison-prefixed display name."""
    raw = (name or "").strip()
    if not raw:
        return "POVISON product"
    if re.match(r"(?i)^povison\b", raw):
        return raw
    return f"Povison {raw}"


def _product_images_html(state: dict, sec: dict) -> str:
    """Render accepted product figures under a section (linked image + name caption)."""
    sid = sec.get("id")
    products = [
        p
        for p in (state.get("products") or [])
        if isinstance(p, dict)
        and (p.get("sectionId") or p.get("section")) == sid
        and p.get("status") != "rejected"
        and p.get("image")
    ]
    if not products:
        return ""
    out = []
    for p in products:
        name = (p.get("name") or "").strip()
        caption = _product_caption(name)
        out.append(
            _centered_image_html(
                url=p["image"],
                alt=name or caption,
                caption=caption,
                credit="",
                max_width=720,
                link_url=(p.get("url") or "").strip(),
            )
        )
    return "".join(out)


def _toc_html(state: dict) -> str:
    """Build a TOC block styled like Rank Math / published POVISON posts.

    Uses an ``h2`` title and body-sized links (not a tiny uppercase label).
    Includes Introduction, body H2s, Conclusion, and nested Q&A questions.
    """
    headings: list[dict] = []
    has_intro = False
    has_conclusion = False
    for sec in state.get("sections") or []:
        stype = sec.get("type")
        if stype == "Intro":
            has_intro = True
        elif stype == "Conclusion":
            has_conclusion = True
        elif sec.get("title"):
            headings.append(
                {"text": sec["title"], "id": _slugify_heading(sec["title"]), "children": []}
            )

    items: list[dict] = []
    if has_intro:
        items.append({"text": "Introduction", "id": "introduction", "children": []})
    items.extend(headings)
    # Editorial Picks: when the standalone POVISON Picks H2 will render (editorial
    # mode + exactly 3 accepted products), surface it in the TOC before Conclusion
    # so readers can jump to the product roundup. Mirrors the _editorial_picks_html
    # activation check so the TOC never advertises a section that won't render.
    # The link text matches the rendered H2 (editorialTitle, or the code fallback)
    # so the TOC entry reads the same heading the reader sees in the body.
    if (state.get("placementStyle") or "inline") == "editorial":
        accepted = [p for p in (state.get("products") or []) if isinstance(p, dict) and p.get("status") == "accepted"]
        if len(accepted) == 3:
            pick_title = (state.get("editorialTitle") or "").strip()
            title_source = "editorialTitle" if pick_title else "fallback"
            if not pick_title:
                topic_title = ((state.get("topic") or {}).get("title") or "").strip()
                pick_title = f"POVISON Picks — {topic_title}" if topic_title else "POVISON Picks"
            items.append({"text": pick_title, "id": "povison-picks", "children": []})
    if has_conclusion:
        items.append({"text": "Conclusion", "id": "conclusion", "children": []})

    faq = state.get("faq") or []
    faq_children = []
    for i, f in enumerate(faq):
        q = str((f or {}).get("q") or "").strip()
        if not q:
            continue
        faq_children.append({"text": q, "id": f"faq-question-{i + 1}"})
    if faq_children:
        items.append({"text": "Q&A", "id": "q-a", "children": faq_children})

    if len(items) < 2:
        return ""

    def _li(entry: dict) -> str:
        kids = entry.get("children") or []
        nested = ""
        if kids:
            nested = (
                "<ul style=\"list-style:disc;padding-left:1.25em;margin:0.35em 0 0;\">"
                + "".join(
                    f'<li style="margin:0.25em 0;"><a href="#{_esc(c["id"])}" '
                    f'style="color:#1a1a1a;text-decoration:underline;font-size:inherit;line-height:inherit;">'
                    f'{_esc(c["text"])}</a></li>'
                    for c in kids
                )
                + "</ul>"
            )
        return (
            f'<li style="margin:0.45em 0;">'
            f'<a href="#{_esc(entry["id"])}" '
            f'style="color:#1a1a1a;text-decoration:underline;font-size:inherit;line-height:inherit;">'
            f'{_esc(entry["text"])}</a>{nested}</li>'
        )

    lis = "".join(_li(it) for it in items)
    return (
        '<div class="wp-block-rank-math-toc-block article-toc" id="rank-math-toc" '
        'style="margin:36px 0;padding:24px 28px;background:#f7f7f7;border:1px solid #e2e2e2;'
        'border-radius:4px;">'
        '<h2 style="font-size:1.5em;font-weight:600;margin:0 0 16px;line-height:1.3;color:#1a1a1a;">'
        "Table of Contents</h2>"
        '<nav><ul style="list-style:disc;padding-left:1.4em;margin:0;'
        'font-size:1.05em;line-height:1.75;color:#1a1a1a;">'
        f"{lis}</ul></nav></div>"
    )


_A_OPEN_TAG_RE = re.compile(r"<a\b[^>]*>", re.I)


def _add_external_link_rel(html: str) -> str:
    """Add ``target="_blank" rel="noreferrer noopener nofollow"`` to external links.

    External = any ``http(s)://`` link whose host is not ``povison.com`` (or a
    subdomain of it). In-page anchors (``#id``) and relative/internal povison.com
    links (product PDPs, collection pages, blog articles injected by placements)
    are left untouched so internal-link SEO juice is not diluted. This mirrors
    the per-link "nofollow" toggle operators would otherwise set by hand in the
    WordPress editor, applied at assembly time so the exported draft ships with
    correct relationships baked in (WordPress preserves ``rel`` on ``<a>`` tags
    received via the REST API).

    Surgical: only rewrites the matched ``<a ...>`` opening tag, leaving the rest
    of the HTML byte-identical so table/figure styles are not disturbed.
    """
    if not html or "<a " not in html.lower():
        return html

    def _process(match: re.Match) -> str:
        tag = match.group(0)
        href_m = re.search(r'\bhref=["\']([^"\']*)["\']', tag, re.I)
        if not href_m:
            return tag
        href = href_m.group(1).strip()
        if href.startswith("#") or not re.match(r"^https?://", href, re.I):
            return tag  # in-page anchor or relative → internal
        host = (urlparse(href).hostname or "").lower()
        if not host or host == "povison.com" or host.endswith(".povison.com"):
            return tag  # internal povison.com link → leave alone
        # Drop any pre-existing target/rel to avoid duplicates, then append ours.
        tag = re.sub(r'\s*target=["\'][^"\']*["\']', "", tag, flags=re.I)
        tag = re.sub(r'\s*rel=["\'][^"\']*["\']', "", tag, flags=re.I)
        if tag.endswith(">"):
            tag = tag[:-1] + ' target="_blank" rel="noreferrer noopener nofollow">'
        else:
            tag = tag + ' target="_blank" rel="noreferrer noopener nofollow">'
        return tag

    return _A_OPEN_TAG_RE.sub(_process, html)


def _editorial_picks_html(state: dict) -> str:
    """Render the ``POVISON Picks`` editorial H2 + 3 H3 product cards.

    Activated only when ``state["placementStyle"] == "editorial"`` AND there are
    exactly 3 accepted products. When not active (inline mode, fewer than 3
    accepted products, or missing fields), returns ``""`` so ``_article_body``
    falls through to the legacy inline assembly — that is the degradation path,
    not an error.

    Structure (matches the POVISON blog reference):
      <h2 id="povison-picks">{editorialTitle}</h2>          # descriptive H2 from
                                                            # topic+outline; falls back to
                                                            # "POVISON Picks — {topic.title}"
                                                            # only when the Agent left it empty
      <p class="editorial-intro">{editorialIntro}</p>        # 50-70 word overview + PDP
                                                            # disclaimer; optional
      for each accepted product:
        <h3 id="...">{product name}</h3>                         # plain text, NO link
        <a href="{pdp url}"><img ...></a>                         # image wraps to PDP
        <p>{blurb}</p>                                           # 90-150 word copy
        <blockquote>{review quote}</blockquote>                  # optional
        <div class="wp-block-button">...See Product Details</div> # Gutenberg core/button CTA → PDP
    """
    if not isinstance(state, dict):
        return ""
    if (state.get("placementStyle") or "inline") != "editorial":
        return ""
    products = [p for p in (state.get("products") or []) if isinstance(p, dict) and p.get("status") == "accepted"]
    if len(products) != 3:
        # Degradation: editorial needs exactly 3 cards. Fewer → render nothing
        # and let _article_body fall back to inline placement per section.
        return ""

    title = (state.get("editorialTitle") or "").strip()
    title_source = "editorialTitle" if title else "fallback"
    if not title:
        topic = state.get("topic") or {}
        topic_title = (topic.get("title") or "").strip()
        title = f"POVISON Picks — {topic_title}" if topic_title else "POVISON Picks"
    intro = (state.get("editorialIntro") or "").strip()
    intro_words = len(intro.split()) if intro else 0

    out = [f'<h2 id="povison-picks">{_esc(title)}</h2>']
    if intro:
        out.append(f'<p class="editorial-intro">{_esc(intro)}</p>')
    for idx, p in enumerate(products, 1):
        out.append(_editorial_card_html(p, idx))
    return "".join(out)


_REVIEW_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _format_review_cite(reviewer: str, date: str) -> str:
    """Build the editorial review citation: "Customer review by {reviewer} ({Month D, YYYY})".

    ``date`` is normally the ISO ``YYYY-MM-DD`` stored on reviewQuote.date (the
    magento2 review DB returns ISO). Already-pretty dates (e.g. the golden
    fixture's "October 17, 2025") are passed through as-is. Falls back
    gracefully when the date is missing or unparseable.
    """
    if not reviewer and not date:
        return ""
    pretty_date = ""
    if date:
        try:
            parts = date.split("-")
            if len(parts) == 3:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    pretty_date = f"{_REVIEW_MONTHS[m - 1]} {d}, {y}"
        except (ValueError, TypeError):
            pretty_date = ""
        # Not ISO → assume already pretty (e.g. "October 17, 2025"); pass through.
        if not pretty_date:
            pretty_date = date
    prefix = "Customer review by " + reviewer if reviewer else "Customer review"
    return f"{prefix} ({pretty_date})" if pretty_date else prefix


def _editorial_card_html(p: dict, idx: int) -> str:
    """Render one editorial product card (H3 + image→PDP + blurb + optional quote).

    The H3 heading is PLAIN TEXT (no link) — the PDP entry point is the image,
    which wraps in ``<a href=PDP>``. This matches the reference blog and keeps
    the visual anchor on the product image rather than the heading text. Reuses
    ``_centered_image_html`` (which already supports ``link_url``) so the WP
    theme + ``wp_publish`` image-download path treats the figure identically to
    inline product images.
    """
    name = (p.get("name") or "").strip()
    image = (p.get("image") or "").strip()
    url = (p.get("url") or "").strip()
    blurb = (p.get("blurb") or "").strip()
    rq = p.get("reviewQuote") if isinstance(p.get("reviewQuote"), dict) else {}

    parts: list[str] = []
    # H3 id must be unique and stable across re-renders.
    heading_id = f"editorial-pick-{idx}"
    parts.append(f'<h3 id="{heading_id}">{_esc(name)}</h3>')
    # Image → PDP. _centered_image_html already wraps both <img> and caption in
    # <a href> when link_url is set; pass link_url=url so the whole figure links.
    if image:
        parts.append(
            _centered_image_html(
                url=image,
                alt=name,
                caption="",
                credit="",
                max_width=760,
                link_url=url,
            )
        )
    if blurb:
        # Blurb is markdown body (may contain inline markdown already). Render to
        # HTML via the same path sections use so links/lists come through.
        parts.append(_chunks_to_html(_section_html(blurb)))
    if rq:
        reviewer = (rq.get("reviewer") or "").strip()
        date = (rq.get("date") or "").strip()
        quote = (rq.get("quote") or "").strip()
        if quote:
            cite = _format_review_cite(reviewer, date)
            cite_html = f"<cite>{_esc(cite)}</cite>" if cite else ""
            # Gutenberg core/quote block markup — `wp-block-quote` is the class
            # WordPress recognizes as the quote block so the theme applies its
            # quote styling (italic/large quote / citation) rather than a plain
            # indented blockquote.
            parts.append(
                f'<blockquote class="wp-block-quote editorial-review">'
                f"<p>{_esc(quote)}</p>{cite_html}</blockquote>"
            )
    # "See Product Details" CTA → PDP. Use the Gutenberg core/button block
    # markup so WordPress (block editor + front-end) recognizes it as a button
    # block. Inline styles guarantee it renders as a visible button in the
    # Studio preview AND in WP when the theme does not style .wp-block-button__link
    # (WP preserves these as the block's style attributes on import).
    if url:
        parts.append(
            f'<div class="wp-block-button aligncenter" style="text-align:center;margin-top:20px;">'
            f'<a class="wp-block-button__link has-text-color has-background" '
            f'href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-block;background-color:#1a1a1a;color:#ffffff;'
            f'padding:12px 28px;border-radius:4px;text-decoration:none;'
            f'font-weight:600;font-size:15px;line-height:1.4;border:2px solid #1a1a1a;">'
            f'See Product Details</a></div>'
        )
    return "".join(parts)


def _article_body(state: dict) -> str:
    """Build article body for WP export / preview.

    Includes TOC (after intro), WP-native centered figures, section images,
    linked product figures, and FAQ (Q&A) microdata so Rank Math can detect FAQ schema.
    """
    body = ""
    toc = _toc_html(state)
    toc_inserted = False
    # Editorial Picks: render the standalone "POVISON Picks" H2 once, just
    # before the Conclusion (or FAQ if no Conclusion). In editorial mode the
    # per-section product image is suppressed (the product lives in the picks
    # block, not trailing the section blurb). Returns "" in inline mode → no-op.
    editorial_html = _editorial_picks_html(state)
    is_editorial = bool(editorial_html)
    editorial_inserted = False
    for sec in state.get("sections") or []:
        # Strip legacy/orphan placement residue, weave accepted internal links
        # inline into body prose (first occurrence), append accepted product
        # blurbs. articleState.products/links is the single source of truth — no
        # manual "写入正文" button.
        sec_content = _prepare_section_content(state, sec)
        chunks = _section_html(sec_content)
        inline_imgs = _inline_images_html(sec)
        product_imgs = "" if is_editorial else _product_images_html(state, sec)
        if sec.get("type") == "Intro":
            intro_html = _chunks_to_html(chunks)
            if not re.search(r"<h2[^>]*>\s*Introduction\s*</h2>", intro_html, re.I):
                body += '<h2 id="introduction">Introduction</h2>'
            else:
                # Ensure TOC can anchor even if agent already wrote the H2.
                intro_html = re.sub(
                    r"<h2([^>]*)>\s*Introduction\s*</h2>",
                    r'<h2\1 id="introduction">Introduction</h2>',
                    intro_html,
                    count=1,
                    flags=re.I,
                )
                if 'id="introduction"' not in intro_html.lower():
                    intro_html = re.sub(
                        r"<h2([^>]*)>",
                        r'<h2\1 id="introduction">',
                        intro_html,
                        count=1,
                        flags=re.I,
                    )
            body += intro_html + inline_imgs
        elif sec.get("type") == "Conclusion":
            if editorial_html and not editorial_inserted:
                body += editorial_html
                editorial_inserted = True
            body += '<div class="conclusion"><h2 id="conclusion">Conclusion</h2>'
            body += _chunks_to_html(chunks)
            body += "</div>"
        else:
            if not toc_inserted and toc:
                body += toc
                toc_inserted = True
            heading_id = _slugify_heading(sec.get("title") or "")
            body += f'<h2 id="{_esc(heading_id)}">{_esc(sec.get("title") or "")}</h2>'
            body += _chunks_to_html(chunks)
            # Image placement rule (operator feedback 2026-07-24): one image per
            # spot — when a section has an accepted product figure, omit the
            # general stock illustration so the two don't stack and separate the
            # product blurb from its image. Product image wins.
            body += product_imgs if product_imgs else inline_imgs
    faq = state.get("faq") or []
    if editorial_html and not editorial_inserted:
        # No Conclusion section existed — insert the picks block before FAQ.
        body += editorial_html
        editorial_inserted = True
    if faq:
        body += f'<h2 id="q-a">{_esc("Q&A")}</h2>' + _faq_block_html(faq)
    # Bake nofollow/target into external links so the WP draft ships with correct
    # link relationships without manual per-link editing in the block editor.
    body = _add_external_link_rel(body)
    return body


def _faq_block_html(faq: list) -> str:
    """Emit FAQ HTML that always renders on the WordPress frontend.

    Do **not** wrap in ``<!-- wp:rank-math/faq-block -->`` unless the block
    comment's ``questions`` JSON exactly matches Rank Math's save() output —
    any mismatch makes Gutenberg report "Block contains unexpected or invalid
    content" and the frontend often renders **nothing** under the FAQ heading
    (exactly what operators saw).

    Plain semantic HTML always displays. Heading is ``Q&A`` (caller). Question
    ``h3`` / answer ``p`` use body-relative sizes to match published posts.
    Schema is injected separately via ``wp_publish._inject_rank_math_faq_schema``.
    """
    items = ""
    for i, f in enumerate(faq or []):
        q = str((f or {}).get("q") or "").strip()
        a = str((f or {}).get("a") or "").strip()
        if not q:
            continue
        qid = f"faq-question-{i + 1}"
        items += (
            f'<div class="faq-item rank-math-list-item" id="{_esc(qid)}" '
            'itemscope itemtype="https://schema.org/Question" '
            'style="border-bottom:1px solid #e8e8e8;padding:8px 0 20px;margin:0;">'
            f'<h3 class="rank-math-question" itemprop="name" '
            f'style="font-size:1.25em;font-weight:600;margin:1.1em 0 0.55em;line-height:1.35;'
            f'color:#1a1a1a;">{_esc(q)}</h3>'
            '<div class="rank-math-answer" itemprop="acceptedAnswer" itemscope '
            'itemtype="https://schema.org/Answer">'
            f'<p itemprop="text" style="font-size:1.05em;color:#333;line-height:1.75;margin:0;">'
            f"{_esc(a)}</p>"
            "</div></div>"
        )
    if not items:
        return ""
    return f'<div class="faq-section" style="margin-top:8px;">{items}</div>'


def _faq_jsonld(state: dict) -> str:
    faq = state.get("faq") or []
    if not faq:
        return ""
    entities = [{"@type": "Question", "name": f.get("q"), "acceptedAnswer": {"@type": "Answer", "text": f.get("a")}} for f in faq]
    blob = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": entities}, ensure_ascii=False, indent=2)
    return f'<script type="application/ld+json">{blob}</script>'


def _category_label(topic: dict) -> str:
    ct = topic.get("content_type") or ""
    if re.search("buying", ct, re.I):
        return "Buying Guide"
    if re.search("comparison", ct, re.I):
        return "Comparison"
    if re.search("scenario|longform", ct, re.I):
        return "Lifestyle"
    return "Guide"


def fill_blog_template(state: dict) -> str:
    tmpl = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else _FALLBACK_TEMPLATE
    m = state.get("meta") or {}
    topic = state.get("topic") or {}
    date = datetime.now().strftime("%B %-d, %Y")
    title = m.get("title") or ""
    hero_idx = abs(hash(title or topic.get("title") or "")) % len(_PEXELS_HEROES)
    hero_cap = (topic.get("title") or "POVISON furniture inspiration")[:80]
    reps = {
        "{{META_TITLE}}": _esc(title),
        "{{META_DESC}}": _esc(m.get("description") or ""),
        "{{CANONICAL_URL}}": _esc(m.get("slug") or ""),
        "{{H1_TITLE}}": _esc(topic.get("title") or title),
        "{{BLOG_CATEGORY}}": _esc(_category_label(topic)),
        "{{AUTHOR}}": "POVISON Editorial",
        "{{DATE}}": date,
        "{{HERO_IMG_URL}}": _PEXELS_HEROES[hero_idx],
        "{{HERO_IMG_CAPTION}}": _esc(hero_cap),
        "{{ARTICLE_BODY}}": _article_body(state),
        "{{FAQ_JSON_LD}}": _faq_jsonld(state),
        "Buying Guide</span>": _category_label(topic) + "</span>",  # the hardcoded category span
    }
    out = tmpl
    for k, v in reps.items():
        out = out.replace(k, v)
    return out


_FALLBACK_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{{META_TITLE}} | POVISON</title>
<meta name="description" content="{{META_DESC}}">
<style>
.editorial-intro { font-style: italic; color: #6b6b6b; margin: 12px 0 24px; }
.wp-block-quote.editorial-review { border-left: 4px solid #8b6f47; padding: 8px 0 8px 20px; margin: 20px 0; color: #6b6b6b; font-style: italic; background: #fbf9f5; border-radius: 0 6px 6px 0; }
.wp-block-quote.editorial-review p { font-size: 17px; line-height: 1.7; }
.wp-block-quote.editorial-review cite { display: block; margin-top: 8px; font-size: 13px; font-style: normal; color: #8b6f47; }
.wp-block-button { margin: 20px 0 8px; }
</style>{{FAQ_JSON_LD}}</head>
<body><article class="article-body">{{ARTICLE_BODY}}</article></body></html>"""


# ---- agent delegation --------------------------------------------------------
_PROGRESS_FILE = "agent-progress.jsonl"
_PROGRESS_TOOL_HINT = (
    "LIVE PROGRESS (required): At EVERY sub-step call tool seo_report_progress with "
    "run_dir=<absolute run dir above>, step=<this step>, task=<what you are doing>, "
    "conclusion=<what you found/decided>, status=running|done|error. "
    "The SEO Studio UI streams these into the operator live panel."
)
_CROSS_PROFILE_WRITE_HINT = (
    "FILE WRITES: The run directory is under the default Hermes home (~/.hermes/skills/...), "
    "but this gateway session runs the povison-seo profile. When using write_file or patch on "
    "any path under the run directory above, you MUST pass cross_profile=true or writes will "
    "be blocked. Prefer writing topics.json / article-state.json directly with cross_profile=true."
)


@app.post("/api/runs/{rid}/agent")
async def agent_run(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    if not GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="HERMES_GATEWAY_KEY unset — start gateway first (./start.sh gateway)")
    step = b.get("step", "unknown")
    run_dir_abs = str(d.resolve())
    keywords = b.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    n_topics = int(b.get("n") or 10)
    # Build step-specific instructions so the Agent writes to the EXACT run dir
    # and updates article-state.json (the file the UI reads), not just markdown.
    step_guidance_map = {
        "brainstorm": (
            f"SERP-driven topic brainstorm for {n_topics} candidates. "
            f"Selected keywords: {', '.join(str(k) for k in keywords) or '(see kw.json)'}. "
            "Use rpa_fetch_google_serp (or browser) on keyword combinations. "
            "Analyze top-10 results for content gaps. "
            "Generate topics per topic-brainstorm-schema v1.0 "
            "(envelope: version/generated_at/input_keywords/serp_queries/topics[] with "
            "id/title/priority/category/content_type/search_intent/primary_keyword/"
            "secondary_keywords/serp_gap/angle required). "
            f"Write {run_dir_abs}/topics.json. Do NOT enter Step 3 / do not pick a winner. "
            "Report progress after each SERP query, gap analysis, topic draft, and scoring."
        ),
        "serp": (
            "Analyze SERP for the topic. Write serp-analysis.md to the run dir, then update "
            "article-state.json: set articleState.serp = {ranks:[...], gaps:[...]} and "
            "articleState.phaseDone.serp = true."
        ),
        "outline": (
            "Generate a blog outline based on the topic and any SERP analysis. Write outline.md "
            "to the run dir, then update article-state.json: set articleState.outline = "
            "[{id, level:'h2'|'h3', text:'...'}, ...] and articleState.phaseDone.outline = true. "
            "Do NOT set outlineConfirmed."
        ),
        "section": (
            "Generate body sections for the confirmed outline. Write to article-state.json: "
            "update articleState.sections[].content and set status='ready' for each. "
            "Set articleState.phaseDone.sections = true when all sections have content. "
            "VOICE/HUMANIZE: after drafting each section, call skill_view('humanizer') and apply its "
            "anti-AI-pattern rules — strip 'stands as a testament', 'underscores the importance', "
            "'in today's fast-paced world', em-dash asides, and other tells; vary sentence length; let "
            "one or two genuine first-person opinions through. Keep POVISON buying-guide credibility "
            "(no slang/drama). Preserve headings, tables, data, product/internal-link markers, and "
            "image_queries — humanize the prose, not the structure. "
            "IMAGES: for each H2 that needs a visual, set image_queries (2-3 concrete English "
            "phrases), run `python3 scripts/search-stock-images.py -q \"...\" -n 5`, pick ONE "
            "candidate that shows furniture/room/layout into section.images=[{url,alt,caption,credit}]. "
            "Skip (images=[]) if no good match — never invent URLs or use moving-box/handshake stock."
        ),
        "faq": (
            "Generate FAQ (4–6 Q&A). Update article-state.json faqs and phaseDone.faq = true."
        ),
        "meta": (
            "Generate SEO meta (title/description/slug). Update article-state.json meta and "
            "phaseDone.meta = true. "
            "HARD LENGTH LIMITS — write to fit, do NOT over-write then truncate: "
            "title 50–60 chars (META title for SERP — rewrite shorter when H1 is long; "
            "do NOT copy H1 verbatim), description 150–160 chars, slug lowercase hyphenated "
            "≤75 chars aligned with the rewritten meta title (not full H1 slug). "
            "Count each field's characters before saving; if out of range, rewrite the wording "
            "(do not save a long string and rely on truncation). Title must contain the primary "
            "keyword naturally near the front; vary description openings (not every article starts "
            "with Discover/Learn/Find out)."
        ),
    }
    step_guidance = step_guidance_map.get(step, f"Step: {step}. Write outputs into the run directory.")

    default_prompt = (
        f"Use skill povison-seo-blog.\n"
        f"Run directory (ABSOLUTE PATH — write all outputs here): {run_dir_abs}\n"
        f"Step: {step}\n"
        f"{step_guidance}\n"
        f"{_PROGRESS_TOOL_HINT}\n"
        f"{_CROSS_PROFILE_WRITE_HINT}\n"
        f"Topic: {b.get('topic_title', '')}\n"
    )
    if step == "brainstorm":
        default_prompt += (
            f"Keywords: {json.dumps(keywords, ensure_ascii=False)}\n"
            f"Generate exactly {n_topics} topics into {run_dir_abs}/topics.json.\n"
            f"Read kw.json in the run dir for SV/KD/CPC when scoring.\n"
            f"When finished call seo_report_progress with status=done and a short summary conclusion.\n"
        )
    else:
        default_prompt += (
            f"IMPORTANT: The UI reads article-state.json from {run_dir_abs}/article-state.json. "
            f"Always update that file in-place (read it, modify the relevant fields, write it back). "
            f"Do NOT write to any other run directory or profile skill path.\n"
            f"Prefer deterministic scripts for IO. When done, list written file paths.\n"
        )
    instructions = b.get("prompt") or default_prompt
    # Reset progress stream for this agent launch so the UI starts clean
    try:
        (d / _PROGRESS_FILE).write_text("", encoding="utf-8")
    except OSError:
        pass
    payload = {
        "input": instructions,
        "instructions": "You are operating the povison-seo-blog skill for the SEO Studio operator.",
        "session_id": f"seo-studio:{rid}",
        "yolo": True,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as cx:
            resp = await cx.post(
                f"{GATEWAY_BASE}/v1/runs",
                json=payload,
                headers={"Authorization": f"Bearer {GATEWAY_KEY}", "Content-Type": "application/json"},
            )
        if resp.status_code not in (200, 202):
            raise HTTPException(status_code=502, detail=f"gateway {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        (d / ".agent_run_id").write_text(data.get("run_id", ""), encoding="utf-8")
        try:
            _db.record_agent_run(rid, data.get("run_id", ""), step)
        except Exception:
            pass
        return {"ok": True, "gateway": data, "run_id": data.get("run_id")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"gateway unreachable: {e}")


@app.get("/api/runs/{rid}/agent/progress")
async def agent_progress(rid: str, since: int = 0) -> dict:
    """Read-only tail of agent-progress.jsonl for live UI streaming.

    Args:
        rid: Run id.
        since: Return lines with idx >= since (default 0 = all).

    Returns:
        ``{ok, lines, total}`` — never mutates state.
    """
    d = _resolve_run(rid)
    path = d / _PROGRESS_FILE
    lines: list[dict] = []
    total = 0
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        for i, row in enumerate(raw.splitlines()):
            row = row.strip()
            if not row:
                continue
            try:
                obj = json.loads(row)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if "idx" not in obj:
                obj["idx"] = i
            total += 1
            if int(obj.get("idx", i)) >= int(since or 0):
                lines.append(obj)
    return {"ok": True, "lines": lines, "total": total}


# ---- DB query endpoints ------------------------------------------------------
@app.get("/api/history")
async def history(limit: int = 50) -> dict:
    return {"runs": _db.list_runs(limit), "stats": _db.stats()}


@app.get("/api/runs")
async def list_runs(limit: int = 50) -> dict:
    """List run directories on disk, newest first, with keyword counts."""
    runs: list[dict] = []
    if RUNS_DIR.exists():
        for d in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
                continue
            rid = d.name
            kw_path = d / "kw.json"
            kw_count = 0
            if kw_path.exists():
                try:
                    data = json.loads(kw_path.read_text(encoding="utf-8"))
                    kw_count = len(data) if isinstance(data, list) else 0
                except Exception:
                    pass
            runs.append({
                "id": rid,
                "path": str(d),
                "kw_count": kw_count,
                "has_topics": (d / "topics.json").exists(),
                "mtime": int(d.stat().st_mtime),
            })
    return {"runs": runs}


@app.get("/api/runs/{rid}/detail")
async def run_detail(rid: str) -> dict:
    detail = _db.run_detail(rid)
    if not detail:
        raise HTTPException(status_code=404, detail="run not in DB")
    return detail


@app.get("/api/db/stats")
async def db_stats() -> dict:
    return _db.stats()


# ---- serve the Studio UI -----------------------------------------------------
@app.get("/")
async def index(request: Request):
    if STUDIO_HTML.exists():
        return FileResponse(str(STUDIO_HTML), media_type="text/html", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    raise HTTPException(status_code=404, detail="Studio HTML not found (set SEO_STUDIO_HTML)")


@app.get("/api/runs/{rid}/agent/status")
async def agent_status(rid: str) -> dict:
    d = _resolve_run(rid)
    marker = d / ".agent_run_id"
    run_id = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if not run_id or not GATEWAY_KEY:
        return {"ok": False, "run_id": run_id, "detail": "no agent run or no gateway key"}
    try:
        async with httpx.AsyncClient(timeout=20) as cx:
            resp = await cx.get(
                f"{GATEWAY_BASE}/v1/runs/{run_id}",
                headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
            )
        if resp.status_code != 200:
            return {"ok": False, "run_id": run_id, "status_code": resp.status_code}
        gw = resp.json()
        # If completed, sync outline.md → article-state.json if agent didn't update it
        if gw.get("status") in ("completed", "succeeded"):
            _sync_agent_outputs(d)
        return {"ok": True, "run_id": run_id, "gateway": gw}
    except httpx.HTTPError as e:
        return {"ok": False, "run_id": run_id, "detail": str(e)}


def _sync_agent_outputs(d: Path) -> None:
    """After agent completes, parse outline.md/serp-analysis.md into article-state.json
    if the agent wrote markdown but didn't update the JSON state."""
    import re as _re
    state_path = d / "article-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}

    changed = False

    # Sync outline.md → article-state.outline
    outline_md = d / "outline.md"
    if outline_md.exists() and not state.get("outline"):
        try:
            text = outline_md.read_text(encoding="utf-8")
            outline = []
            oid = 1
            for line in text.splitlines():
                line = line.rstrip()
                if line.startswith("### H2:") or line.startswith("## H2:"):
                    title = _re.sub(r"^(?:###|##)\s*H2:\s*", "", line).strip()
                    title = _re.sub(r"\s*\(.*?\)\s*$", "", title)
                    outline.append({"id": oid, "level": "h2", "text": title})
                    oid += 1
                elif line.startswith("### H1:") or line.startswith("## H1:"):
                    title = _re.sub(r"^(?:###|##)\s*H1:\s*", "", line).strip()
                    outline.append({"id": oid, "level": "h2", "text": title})
                    oid += 1
                elif line.startswith("#### H3:") or line.startswith("### H3:"):
                    title = _re.sub(r"^(?:####|###)\s*H3:\s*", "", line).strip()
                    outline.append({"id": oid, "level": "h3", "text": title})
                    oid += 1
                elif line.startswith("### Introduction") or line.startswith("## Introduction"):
                    outline.append({"id": oid, "level": "h2", "text": "Introduction"})
                    oid += 1
                elif line.startswith("### Conclusion") or line.startswith("## Conclusion"):
                    outline.append({"id": oid, "level": "h2", "text": "Conclusion"})
                    oid += 1
                elif line.startswith("### FAQ Section") or line.startswith("## FAQ"):
                    outline.append({"id": oid, "level": "h2", "text": "FAQ"})
                    oid += 1
            if outline:
                state["outline"] = outline
                state["outlineConfirmed"] = False
                pd = state.setdefault("phaseDone", {})
                pd["serp"] = True
                pd["outline"] = True
                changed = True
        except Exception:
            pass

    # Sync serp-analysis.md → article-state.serp (basic)
    serp_md = d / "serp-analysis.md"
    if serp_md.exists() and not state.get("serp"):
        state.setdefault("phaseDone", {})["serp"] = True
        state["serp"] = {"ranks": [], "gaps": [], "source": "agent"}
        changed = True

    if changed:
        try:
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
