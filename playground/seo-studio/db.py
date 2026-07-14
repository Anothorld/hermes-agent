"""SQLite record layer for the POVISON SEO Studio workflow.

Records workflow events alongside the file-based run artifacts (files remain
the handoff source of truth; the DB is the queryable index/history).

Schema:
  runs            — one row per run directory
  keywords        — keyword rows tied to a run (post-enrich)
  topics          — brainstormed topics tied to a run
  article_state   — latest article-state snapshot summary per run
  audit_log       — every script job + agent call + UI gate
  agent_runs      — gateway run delegations
  generation_rules— per-run rule snapshot (block, text, enabled)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _db_path() -> Path:
    return Path(__file__).resolve().parent / "seo_studio.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _LOCK, get_conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                label TEXT,
                path TEXT,
                status TEXT DEFAULT 'active',
                parent_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS keywords (
                run_id TEXT NOT NULL,
                text TEXT NOT NULL,
                source TEXT,
                type TEXT,
                freq INTEGER,
                sv REAL, kd REAL, cpc REAL, intent TEXT,
                metrics_status TEXT,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS topics (
                run_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                title TEXT, priority TEXT, category TEXT,
                content_type TEXT, primary_keyword TEXT,
                priority_score REAL, serp_gap TEXT,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS article_state (
                run_id TEXT PRIMARY KEY,
                topic_title TEXT,
                phase TEXT,
                word_count INTEGER,
                sections_ready INTEGER,
                products INTEGER, links INTEGER, faq_count INTEGER,
                meta_title TEXT, meta_slug TEXT,
                validation_passed INTEGER, validation_total INTEGER,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step TEXT, action TEXT, detail TEXT,
                status TEXT, returncode INTEGER, error TEXT,
                ts TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT NOT NULL,
                gateway_run_id TEXT,
                step TEXT,
                status TEXT,
                requested_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS generation_rules (
                run_id TEXT NOT NULL,
                block TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_kw_run ON keywords(run_id);
            CREATE INDEX IF NOT EXISTS idx_topics_run ON topics(run_id);
            CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
            """
        )


# ---- writers ----------------------------------------------------------------
def record_run(rid: str, path: str, label: str | None = None, parent_id: str | None = None) -> None:
    with _LOCK, get_conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO runs(id,label,path,status,parent_id,created_at) VALUES(?,?,?,?,?,?)",
            (rid, label, str(path), "active", parent_id, _now()),
        )


def touch_run(rid: str, status: str | None = None) -> None:
    with _LOCK, get_conn() as c:
        if status:
            c.execute("UPDATE runs SET status=?, updated_at=? WHERE id=?", (status, _now(), rid))
        else:
            c.execute("UPDATE runs SET updated_at=? WHERE id=?", (_now(), rid))


def record_keywords(rid: str, keywords: list[dict[str, Any]]) -> None:
    with _LOCK, get_conn() as c:
        c.execute("DELETE FROM keywords WHERE run_id=?", (rid,))
        now = _now()
        for k in keywords:
            c.execute(
                "INSERT INTO keywords(run_id,text,source,type,freq,sv,kd,cpc,intent,metrics_status,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid, k.get("text"), k.get("source"), k.get("type"), k.get("freq"),
                    k.get("sv"), k.get("kd"), k.get("cpc"), k.get("intent"),
                    k.get("metrics_status"), now,
                ),
            )


def record_topics(rid: str, topics: list[dict[str, Any]]) -> None:
    with _LOCK, get_conn() as c:
        c.execute("DELETE FROM topics WHERE run_id=?", (rid,))
        now = _now()
        for t in topics:
            c.execute(
                "INSERT INTO topics(run_id,topic_id,title,priority,category,content_type,primary_keyword,priority_score,serp_gap,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rid, str(t.get("id") or t.get("schema_id") or ""),
                    t.get("title"), t.get("priority"), t.get("category"),
                    t.get("content_type"), t.get("primary_keyword"),
                    t.get("priority_score"), t.get("serp_gap"), now,
                ),
            )


def record_article_state(rid: str, state: dict[str, Any]) -> None:
    sections = state.get("sections") or []
    body = "\n".join(s.get("content") or "" for s in sections)
    wc = len((body or "").split())
    ready = sum(1 for s in sections if s.get("content") and s.get("status") in ("ready", "edited"))
    products = [p for p in (state.get("products") or []) if p.get("status") == "accepted"]
    links = [l for l in (state.get("links") or []) if l.get("status") == "accepted"]
    faq = state.get("faq") or []
    meta = state.get("meta") or {}
    v = state.get("validation") or {}
    phase = "preview" if state.get("phaseDone", {}).get("preview") else (
        "meta" if state.get("phaseDone", {}).get("meta") else (
        "faq" if state.get("phaseDone", {}).get("faq") else (
        "placements" if state.get("phaseDone", {}).get("placements") else (
        "sections" if state.get("phaseDone", {}).get("sections") else (
        "outline" if state.get("phaseDone", {}).get("outline") else "serp")))))
    with _LOCK, get_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO article_state("
            "run_id,topic_title,phase,word_count,sections_ready,products,links,faq_count,"
            "meta_title,meta_slug,validation_passed,validation_total,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid, (state.get("topic") or {}).get("title"), phase, wc, ready,
                len(products), len(links), len(faq),
                meta.get("title"), meta.get("slug"),
                v.get("passed"), v.get("total"), _now(),
            ),
        )
    touch_run(rid)


def record_audit(rid: str, step: str, action: str, detail: str = "", status: str = "", returncode: int | None = None, error: str = "") -> None:
    with _LOCK, get_conn() as c:
        c.execute(
            "INSERT INTO audit_log(run_id,step,action,detail,status,returncode,error,ts) VALUES(?,?,?,?,?,?,?,?)",
            (rid, step, action, detail, status, returncode, error, _now()),
        )
    touch_run(rid)


def record_agent_run(rid: str, gateway_run_id: str, step: str, status: str = "requested") -> None:
    with _LOCK, get_conn() as c:
        c.execute(
            "INSERT INTO agent_runs(run_id,gateway_run_id,step,status,requested_at) VALUES(?,?,?,?,?)",
            (rid, gateway_run_id, step, status, _now()),
        )
    record_audit(rid, "agent", step, f"gateway_run={gateway_run_id}", status)


def record_generation_rules(rid: str, rules_doc: dict[str, Any]) -> None:
    blocks = rules_doc.get("blocks") or rules_doc
    with _LOCK, get_conn() as c:
        c.execute("DELETE FROM generation_rules WHERE run_id=?", (rid,))
        now = _now()
        for bid, block in blocks.items():
            rules = block.get("rules") if isinstance(block, dict) else block
            if not isinstance(rules, list):
                continue
            for r in rules:
                c.execute(
                    "INSERT INTO generation_rules(run_id,block,rule_text,enabled,recorded_at) VALUES(?,?,?,?,?)",
                    (rid, bid, str(r.get("text") or ""), 1 if r.get("enabled", True) else 0, now),
                )


# ---- readers ----------------------------------------------------------------
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT r.id, r.label, r.status, r.created_at, r.updated_at, "
            "a.phase, a.word_count, a.validation_passed, a.validation_total "
            "FROM runs r LEFT JOIN article_state a ON a.run_id=r.id "
            "ORDER BY r.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def run_detail(rid: str) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        out = dict(r)
        out["keywords"] = [dict(x) for x in c.execute("SELECT * FROM keywords WHERE run_id=?", (rid,)).fetchall()]
        out["topics"] = [dict(x) for x in c.execute("SELECT * FROM topics WHERE run_id=?", (rid,)).fetchall()]
        out["article_state"] = [dict(x) for x in c.execute("SELECT * FROM article_state WHERE run_id=?", (rid,)).fetchall()]
        out["audit_log"] = [dict(x) for x in c.execute("SELECT * FROM audit_log WHERE run_id=? ORDER BY id", (rid,)).fetchall()]
        out["agent_runs"] = [dict(x) for x in c.execute("SELECT * FROM agent_runs WHERE run_id=?", (rid,)).fetchall()]
        return out


def stats() -> dict[str, Any]:
    with get_conn() as c:
        return {
            "runs": c.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "keywords": c.execute("SELECT COUNT(*) FROM keywords").fetchone()[0],
            "topics": c.execute("SELECT COUNT(*) FROM topics").fetchone()[0],
            "audit_events": c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            "agent_runs": c.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
            "articles_tracked": c.execute("SELECT COUNT(*) FROM article_state").fetchone()[0],
            "db_path": str(_db_path()),
        }


# Initialize on import.
init_db()
