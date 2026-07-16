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

import httpx
import sys
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from auth import feishu_h5_client, oidc_client, operator_session, operator_store
from auth.oidc_routes import router as oidc_router

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
            proc = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
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
    return {"ok": True, "step": step}


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
            "{id, type, title, content, status, subheads?, transition?} where "
            "type is one of 'Intro' (capitalized) for the intro, 'h2' for body sections, "
            "'Conclusion' (capitalized) for the conclusion; title is the section heading text "
            "(empty string for Intro); content is the markdown body; status='ready' when done. "
            "Set articleState.phaseDone.sections = true when all sections have content.\n"
            "IMAGES: For each section that warrants a visual (not every short section needs one), "
            "find 1 copyright-free image from Unsplash or Pexels (use web search or browser to get "
            "the direct image URL — e.g. https://images.unsplash.com/photo-... or "
            "https://images.pexels.com/.../...jpeg). Attach them to the section as "
            "section.images = [{url, alt, caption, credit}]. Rules:\n"
            "- Source MUST be Unsplash or Pexels (license-free). NEVER use POVISON product photos here.\n"
            "- alt = descriptive alt text; caption = short figure caption; credit = 'Photo: Unsplash' or 'Photo: Pexels'.\n"
            "- Pick images that match the section's topic; skip if no good match (don't force a wrong image).\n"
            "- Do NOT duplicate an image across sections."
        ),
        "faq": (
            "Generate FAQ (4-6 Q&A). Update articleState.faqs and set articleState.phaseDone.faq = true."
        ),
        "meta": (
            "Generate SEO meta (title/description/slug). Update articleState.meta and "
            "set articleState.phaseDone.meta = true."
        ),
        "placements": (
            "Decide product placements and internal links. "
            "Each articleState.products[] item MUST use this schema: "
            "{id, name, url, sectionId, blurb, status, image} where sectionId is the "
            "section id this product attaches to (matches sections[].id); blurb is the "
            "40-70 word placement copy; status is 'pending'; image is the POVISON product "
            "image URL (empty string if not found). Update articleState.products and "
            "articleState.internalLinks (or links), then set articleState.phaseDone.placements = true.\n"
            "PRODUCT IMAGES: For each product in articleState.products, attach a real product image "
            "from POVISON. Use the povison_product tool or browse the POVISON product page to get the "
            "product's main image URL, then set product.image = <direct image URL>. Rules:\n"
            "- Product images MUST come from POVISON (povison.com) — NEVER from Unsplash/Pexels.\n"
            "- This keeps body images (Unsplash/Pexels lifestyle) and product images (POVISON product shots) "
            "from conflicting — they are always from different sources.\n"
            "- If a product image URL cannot be found, leave product.image = '' rather than substituting a stock photo."
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


@app.post("/api/tasks/{task_id}/wordpress/draft")
async def wordpress_draft(task_id: str, body: dict | None = None) -> dict:
    """Export the task's article to WordPress as a draft.

    Builds the full blog-template HTML from the DB step-3 articleState, then
    delegates to the operator's ``wordpress_mcp`` publisher (same pipeline the
    agent uses via MCP). The parser extracts ``<article class="article-body">``
    as post content and reads title/slug/meta-description/FAQ-schema from the
    head. Images are referenced by their original URL (no media sideload) for
    the MVP — pass ``skip_image_upload: false`` in the body to sideload them.
    """
    _task_or_404(task_id)
    b = body or {}
    state = _db.get_step_data(task_id, 3)
    if not state or not (state.get("topic") or (state.get("meta") or {}).get("title")):
        raise HTTPException(status_code=400, detail="no article to export — generate content first")
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
            skip_image_upload=bool(b.get("skip_image_upload", True)),
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


def _section_html(content: str) -> list[str]:
    out = []
    for line in (content or "").splitlines():
        t = _esc(line)
        t = re.sub(r"\[Product:\s*[^\]]+\]", "", t)
        t = re.sub(r"\[Internal link:[^\]]+\]", "", t)
        t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
        t = t.strip()
        if not t:
            continue
        if t.startswith("•") or t.startswith("·"):
            out.append(f"<li>{t[1:].strip()}</li>")
        else:
            out.append(f"<p>{t}</p>")
    return out


def _article_body(state: dict) -> str:
    body = ""
    for sec in state.get("sections") or []:
        chunks = _section_html(sec.get("content") or "")
        if sec.get("type") == "Intro":
            in_list = False
            for c in chunks:
                if c.startswith("<li>"):
                    if not in_list:
                        body += "<ul>"; in_list = True
                    body += c
                else:
                    if in_list:
                        body += "</ul>"; in_list = False
                    body += c
            if in_list:
                body += "</ul>"
        elif sec.get("type") == "Conclusion":
            body += '<div class="conclusion"><h2>Conclusion</h2>'
            for c in chunks:
                body += f"<ul>{c}</ul>" if c.startswith("<li>") else c
            body += "</div>"
        else:
            body += f'<h2>{_esc(sec.get("title") or "")}</h2>'
            in_list = False
            for c in chunks:
                if c.startswith("<li>"):
                    if not in_list:
                        body += "<ul>"; in_list = True
                    body += c
                else:
                    if in_list:
                        body += "</ul>"; in_list = False
                    body += c
            if in_list:
                body += "</ul>"
    faq = state.get("faq") or []
    if faq:
        body += '<section class="faq-section"><h2>Frequently Asked Questions</h2>'
        for f in faq:
            body += f'<div class="faq-item"><h3 onclick="toggleFaq(this)">{_esc(f.get("q") or "")}</h3><p>{_esc(f.get("a") or "")}</p></div>'
        body += "</section>"
    return body


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
            "Set articleState.phaseDone.sections = true when all sections have content."
        ),
        "faq": (
            "Generate FAQ (4–6 Q&A). Update article-state.json faqs and phaseDone.faq = true."
        ),
        "meta": (
            "Generate SEO meta (title/description/slug). Update article-state.json meta and "
            "phaseDone.meta = true."
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
