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

_DEBUG_LOG = Path("/Users/arnold/agent_prj/.cursor/debug-5d4e3c.log")
_DEBUG_ENDPOINT = "http://127.0.0.1:7552/ingest/6dae660f-ff9f-42cd-9716-19333bd7e7cb"

def _dbg(location: str, message: str, data: dict | None = None, hid: str = "H4") -> None:
    try:
        import time as _t
        payload = {"sessionId": "5d4e3c", "id": f"log_{int(_t.time()*1000)}", "timestamp": int(_t.time() * 1000), "location": location, "message": message, "data": data or {}, "runId": "repro2", "hypothesisId": hid}
        # Prefer HTTP ingest (avoids .cursor/ file permission issues); fall back to file.
        try:
            httpx.post(_DEBUG_ENDPOINT, json=payload, headers={"X-Debug-Session-Id": "5d4e3c"}, timeout=2)
        except Exception:
            with _DEBUG_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
    except Exception:
        pass

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


def _spawn_job(kind: str, cmd: list[str], cwd: Path, rid: str = "", on_done=None) -> str:
    """Run a subprocess in a thread; return job_id immediately."""
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
            # #region agent log
            try:
                _dbg(f"server.py:_spawn_job:{kind}", "JOB_DONE", {
                    "job_id": job_id, "kind": kind, "run_id": rid, "status": status,
                    "returncode": rc,
                    "stderr_tail": (proc.stderr or "")[-800:] if proc else "",
                    "stdout_tail": (proc.stdout or "")[-400:] if proc else "",
                    "error": err[:400],
                }, "H1")
            except Exception:
                pass
            # #endregion agent log

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def _py() -> str:
    return sys.executable


# ---- health ------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request) -> dict:
    _dbg("server.py:health", "HEALTH_HIT", {"origin": request.headers.get("origin"), "user_agent": request.headers.get("user-agent", "")[:60]}, "H4")
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
)


def _backfill_empty_content_fields(data: dict, db_data: dict | None) -> int:
    """Backfill empty content fields in ``data`` from ``db_data``.

    Returns the number of fields backfilled (0 = no change). Only fields listed
    in ``_ARTICLE_CONTENT_FIELDS`` are considered; ``products``/``links`` are
    untouched. Mutates ``data`` in place.
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
    return n


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
    # earlier sub-step's already-generated output. `products`/`links` are not
    # touched (empty is meaningful there).
    backfilled = 0
    if step_num == 3 and isinstance(data, dict):
        existing = _db.get_step_data(task_id, step_num)
        backfilled = _backfill_empty_content_fields(data, existing)
    # Root-cause fix for 404 placements: validate product/link URLs on save.
    # If any URL looks fabricated, block placementsConfirmed and surface warnings.
    placement_warnings: list[dict] = []
    if step_num == 3 and isinstance(data, dict):
        placement_warnings = _validate_placement_urls(data)
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
        "section": (
            "Generate body sections for the confirmed outline. "
            "Each articleState.sections[] item MUST use this schema: "
            "{id, type, title, content, status, image_queries?, images?, subheads?, transition?} where "
            "type is one of 'Intro' (capitalized) for the intro, 'h2' for body sections, "
            "'Conclusion' (capitalized) for the conclusion; title is the section heading text "
            "(empty string for Intro); content is the markdown body; status='ready' when done. "
            "Set articleState.phaseDone.sections = true when all sections have content.\n"
            "VOICE / HUMANIZE (REQUIRED before saving each section):\n"
            "1) After drafting each section's content, call `skill_view('humanizer')` and apply its "
            "anti-AI-pattern rules to rewrite the prose so it sounds like a real person wrote it — "
            "strip 'stands as a testament', 'underscores the importance', 'in today's fast-paced world', "
            "'it's not just X, it's Y', em-dash asides, and other tells the skill lists.\n"
            "2) Keep POVISON buying-guide credibility: first-person experience and opinions are welcome, "
            "but stay trustworthy and specific — no stand-up bits, no slang, no manufactured drama. "
            "Vary sentence length, prefer concrete details over filler, and let one or two genuine "
            "opinions through per section.\n"
            "3) Preserve all SEO structure: keep H2/H3 headings, tables, data citations, product/internal-link "
            "markers, image_queries, and section boundaries intact — humanize the prose, not the structure.\n"
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
        ),
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
        "placements": (
            "Decide product placements and internal links. "
            "Each articleState.products[] item MUST use this schema: "
            "{id, name, url, sectionId, blurb, status, image} where sectionId is the "
            "section id this product attaches to (matches sections[].id); blurb is the "
            "40-70 word placement copy; status is 'pending'; image is the POVISON product "
            "image URL (empty string if not found). Update articleState.products and "
            "articleState.internalLinks (or links), then set articleState.phaseDone.placements = true.\n"
            "HARD RULE — NEVER FABRICATE URLs (root-cause fix for 404 placements). You MUST produce BOTH products and internal links in this single placements run by calling the two tools below. Do NOT defer to the operator buttons — YOU call the tools and write the results.\n"
            "STEP 1 — PRODUCTS: Call "
            "`python3 scripts/povison-catalog.py recommend --topic '<JSON>' --sections <file> --limit 2` "
            "(or POST http://127.0.0.1:8766/api/povison-products/recommend with "
            "{topic:{primary_keyword,secondary_keywords,category_keywords}, sections, limit}). "
            "Write the returned products[] straight into articleState.products (each item already has "
            "name, url, image, sku, fit_score, sectionId, blurb — keep them; set status='pending'). "
            "Real PDP URLs look like `https://www.povison.com/<slug>.html?variant=<id>` — they ALWAYS "
            "contain `.html` and a `?variant=` param. If a URL you'd write does not match this shape, it "
            "is fabricated and will 404 — DO NOT write it.\n"
            "STEP 2 — INTERNAL LINKS: Call "
            "`python3 scripts/povison-blog.py recommend-links --topic '<JSON>' --sections <file> --limit 3` "
            "(or POST http://127.0.0.1:8766/api/povison-blog/recommend-links with "
            "{topic, sections, existing_urls, limit}). Write the returned links[] straight into "
            "articleState.links (each item has anchor, url, sectionId, score, reasons — keep them; "
            "set status='pending'). Real blog URLs look like "
            "`https://www.povison.com/blog/<category>/<slug>.html` — they ALWAYS start with "
            "`https://www.povison.com/blog/` and end with `.html`. Never invent a blog URL; if the API "
            "returns no good match, write fewer links (0-1) rather than a fabricated one.\n"
            "STEP 3 — SAVE: REPLACE articleState.products and articleState.links entirely with the "
            "API results (do not keep any previous products/links from the loaded articleState — "
            "the operator may have deleted them before re-running). Write BOTH into articleState "
            "in one seo_save_step_data call, then set articleState.phaseDone.placements = true.\n"
            "PRODUCT IMAGES: The catalog API already fills image (Detail API main image). Do NOT "
            "substitute stock photos for product images. If a product image is missing, leave image=''.\n"
            "INTERNAL LINK anchors: anchor text MUST be a VERBATIM long-tail phrase that already appears "
            "word-for-word in the target section's `content` (the section is already written — read it). "
            "The recommend-links API returns a suggested anchor derived from the article slug (e.g. "
            "'why-choose-a-sintered-stone-dining-table'); that slug-derived anchor almost never appears "
            "verbatim in the body, so you MUST scan the section's actual prose and pick a real phrase from it "
            "(e.g. if the section contains 'a sintered stone dining table adds texture', use "
            "'sintered stone dining table' as the anchor). Never use 'click here' / 'see this guide' / "
            "'Povison blog'. If no suitable verbatim phrase exists in that section, either pick a different "
            "section for the link or use a shorter phrase that does appear. The assembly step weaves the "
            "link inline at the first occurrence of the anchor in the body; if the anchor is not verbatim in "
            "the body it falls back to a trailing 'Related:' footnote, which reads worse — so pick carefully."
        ),
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
    # #region agent log
    _dbg("server.py:put_file", "PUT_FILE", {"rid": rid, "name": name, "body_type": type(body).__name__, "body_is_none": body is None, "body_len": len(body) if isinstance(body, (list, str, dict)) else None, "raw_head": raw[:80]}, "H2")
    # #endregion agent log
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
    # #region agent log
    _dbg("server.py:keywords_discover", "DISCOVER_HIT", {"rid": rid, "sources": b.get("sources"), "min_freq": b.get("min_freq", 2)}, "H1")
    # #endregion agent log
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
    job_id = _spawn_job("keyword-discovery", cmd, d, rid)
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
    job_id = _spawn_job("enrich-keyword-metrics", cmd, d, rid, _on_enrich_done)
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
    # #region agent log
    _dbg("server.py:topics_brainstorm", "BRAINSTORM_HIT", {"rid": rid, "kw_input": kw_path.name, "n": b.get("n", 10), "demo": b.get("demo")}, "H2")
    # #endregion agent log
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
    job_id = _spawn_job("topic-brainstorm", cmd, d, rid, _on_bs_done)
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
    def _on_sec_done():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            _db.record_article_state(rid, st)
        except Exception:
            pass
    job_id = _spawn_job("section-generate", cmd, d, rid, _on_sec_done)
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


def _prepare_section_content(state: dict, sec: dict) -> str:
    """Clean legacy placement residue, inline-link accepted internal links into
    body prose, then append accepted product blurbs once.

    Internal links are woven into the section's first plain-text occurrence of
    their anchor (case-insensitive, word-bounded, preserves the body's original
    casing) so they read like editor-placed inline links rather than trailing
    footnotes. Links whose anchor does not appear in the body fall back to a
    trailing ``Related: [anchor](url)`` line so no accepted link is silently
    dropped. Products are still appended as blurbs at the end (their blurb prose
    is not part of the section body, so inline weaving does not apply).
    """
    sec_content = _strip_legacy_placements(sec.get("content") or "")
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


def _article_body(state: dict) -> str:
    """Build article body for WP export / preview.

    Includes TOC (after intro), WP-native centered figures, section images,
    linked product figures, and FAQ (Q&A) microdata so Rank Math can detect FAQ schema.
    """
    body = ""
    toc = _toc_html(state)
    toc_inserted = False
    for sec in state.get("sections") or []:
        # Strip legacy/orphan placement residue, weave accepted internal links
        # inline into body prose (first occurrence), append accepted product
        # blurbs. articleState.products/links is the single source of truth — no
        # manual "写入正文" button.
        sec_content = _prepare_section_content(state, sec)
        chunks = _section_html(sec_content)
        inline_imgs = _inline_images_html(sec)
        product_imgs = _product_images_html(state, sec)
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
            body += inline_imgs + product_imgs
    faq = state.get("faq") or []
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
<meta name="description" content="{{META_DESC}}">{{FAQ_JSON_LD}}</head>
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
    _dbg("server.py:index", "INDEX_HIT", {"origin": request.headers.get("origin"), "ua": request.headers.get("user-agent", "")[:60]}, "H4")
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
