"""POVISON SEO Studio Bridge — single-file FastAPI backend.

Serves the Studio UI, wraps the skill's deterministic Python scripts as
background jobs, and delegates open-ended SEO work to the povison-seo
Hermes Gateway (``POST /v1/runs``).

Started by ``start.sh`` as::

    python -m uvicorn server:app --host 127.0.0.1 --port 8765

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

# Local dev: allow any origin (file:// standalone + http://127.0.0.1:8765).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def _py() -> str:
    return sys.executable


# ---- health ------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request) -> dict:
    _dbg("server.py:health", "HEALTH_HIT", {"origin": request.headers.get("origin"), "user_agent": request.headers.get("user-agent", "")[:60]}, "H4")
    scripts_ok = (SCRIPTS / "section-generate.py").exists() and (SCRIPTS / "validate-article.py").exists()
    return {
        "ok": True,
        "profile": PROFILE,
        "scripts_ok": scripts_ok,
        "gateway_key_set": bool(GATEWAY_KEY),
        "skill_dir": str(SKILL_DIR),
        "runs_dir": str(RUNS_DIR),
    }


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
async def put_file(rid: str, name: str, body: Any = None):
    d = _resolve_run(rid)
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, (dict, list)):
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif isinstance(body, str):
        p.write_text(body, encoding="utf-8")
    else:
        p.write_text(str(body), encoding="utf-8")
    # Record structured artifacts into the DB for queryable history.
    try:
        if name == "kw.json" and isinstance(body, list):
            _db.record_keywords(rid, body)
        elif name == "topics.json" and isinstance(body, dict):
            _db.record_topics(rid, body.get("topics") or [])
        elif name == "article-state.json" and isinstance(body, dict):
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
        "--catalog", str(catalog),
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
_PEXELS_GALLERY = [
    "https://images.pexels.com/photos/1571460/pexels-photo-1571460.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/1648776/pexels-photo-1648776.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/276583/pexels-photo-276583.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/1080721/pexels-photo-1080721.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/6492397/pexels-photo-6492397.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/6444/timeline-clothing-rack-pants.jpg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/1668860/pexels-photo-1668860.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/3935350/pexels-photo-3935350.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/2253870/pexels-photo-2253870.jpeg?auto=compress&cs=tinysrgb&w=600",
    "https://images.pexels.com/photos/4498596/pexels-photo-4498596.jpeg?auto=compress&cs=tinysrgb&w=600",
]
_GALLERY_CAPTIONS = [
    "Living room layout ideas", "Bedroom storage solutions", "Dining setup for two",
    "Workspace corner", "Entryway bench", "Closet edit",
    "Reading nook", "Open shelving", "Neutral palette", "Modular sofa",
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


def _gallery_items() -> str:
    html = ""
    for i in range(10):
        url = _PEXELS_GALLERY[i % len(_PEXELS_GALLERY)]
        cap = _GALLERY_CAPTIONS[i % len(_GALLERY_CAPTIONS)]
        html += f'<a class="gallery-item" href="{url}" target="_blank"><img src="{url}" alt="{_esc(cap)}" loading="lazy"><p>{_esc(cap)}</p></a>'
    return html


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
        "{{GALLERY_ITEMS}}": _gallery_items(),
        "Buying Guide</span>": _category_label(topic) + "</span>",  # the hardcoded category span
    }
    out = tmpl
    for k, v in reps.items():
        out = out.replace(k, v)
    return out


_FALLBACK_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{{META_TITLE}} | POVISON</title>
<meta name="description" content="{{META_DESC}}">{{FAQ_JSON_LD}}</head>
<body><article class="article-body">{{ARTICLE_BODY}}</article>
<section class="image-gallery"><h2>Visual Inspiration</h2><div class="gallery-grid">{{GALLERY_ITEMS}}</div></section></body></html>"""


# ---- agent delegation --------------------------------------------------------
@app.post("/api/runs/{rid}/agent")
async def agent_run(rid: str, body: dict | None = None) -> dict:
    d = _resolve_run(rid)
    b = body or {}
    if not GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="HERMES_GATEWAY_KEY unset — start gateway first (./start.sh gateway)")
    instructions = (
        b.get("prompt")
        or f"Use skill povison-seo-blog. Run directory: {d}. Step: {b.get('step', 'unknown')}. "
        "Prefer deterministic scripts for IO; write outputs into the run directory. When done, list written file paths."
    )
    payload = {
        "input": instructions,
        "instructions": "You are operating the povison-seo-blog skill for the SEO Studio operator.",
        "session_id": f"seo-studio:{rid}",
        "yolo": bool(b.get("yolo", False)),
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
            _db.record_agent_run(rid, data.get("run_id", ""), str(b.get("step", "unknown")))
        except Exception:
            pass
        return {"ok": True, "gateway": data, "run_id": data.get("run_id")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"gateway unreachable: {e}")


# ---- DB query endpoints ------------------------------------------------------
@app.get("/api/history")
async def history(limit: int = 50) -> dict:
    return {"runs": _db.list_runs(limit), "stats": _db.stats()}


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
        return {"ok": True, "run_id": run_id, "gateway": resp.json()}
    except httpx.HTTPError as e:
        return {"ok": False, "run_id": run_id, "detail": str(e)}
