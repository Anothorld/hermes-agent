"""SQLite source of truth for the POVISON SEO Studio workflow.

Each **task** has a task_id and three **steps** (keywords / brainstorm / generation).
Step payloads live in ``steps.data_json``. Branching from step N copies steps 1..N
into a new task.

Task ``status`` values (operator-facing):
  idle       — 闲置 (default; also after opening a completed task)
  running    — 运行中 (a script/agent job is in flight)
  completed  — 已完成 (step 3 done; flips to idle when opened in the UI)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

TASK_STATUSES = ("idle", "running", "completed")
STEP_TYPES = {1: "keywords", 2: "brainstorm", 3: "generation"}
STEP_STATUSES = ("pending", "running", "done", "error")


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
        # Detect legacy schema (runs table without tasks) and migrate by renaming.
        legacy = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        has_tasks = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if legacy and not has_tasks:
            for tbl in (
                "runs", "keywords", "topics", "article_state", "audit_log",
                "agent_runs", "generation_rules", "agent_progress",
            ):
                try:
                    c.execute(f"ALTER TABLE {tbl} RENAME TO _legacy_{tbl}")
                except sqlite3.OperationalError:
                    pass
        # If agent_progress / audit_log exist but lack task_id (legacy), rebuild them.
        for tbl in ("agent_progress", "audit_log", "steps"):
            exists = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            ).fetchone()
            if not exists:
                continue
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "task_id" not in cols:
                c.execute(f"DROP TABLE IF EXISTS {tbl}")

        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                parent_task_id TEXT,
                fork_step INTEGER,
                label TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS steps (
                step_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_num INTEGER NOT NULL,
                step_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                data_json TEXT,
                parent_step_id TEXT,
                agent_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                UNIQUE(task_id, step_num)
            );
            CREATE TABLE IF NOT EXISTS agent_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_num INTEGER NOT NULL,
                idx INTEGER NOT NULL,
                ts TEXT NOT NULL,
                step TEXT,
                task_desc TEXT,
                conclusion TEXT,
                status TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_num INTEGER,
                action TEXT,
                detail TEXT,
                status TEXT,
                ts TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
            CREATE INDEX IF NOT EXISTS idx_progress_task ON agent_progress(task_id, step_num, idx);
            CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """
        )


def _new_task_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"task-{stamp}-{uuid.uuid4().hex[:6]}"


def _step_id(task_id: str, step_num: int) -> str:
    return f"{task_id}-s{step_num}"


# ---- tasks ------------------------------------------------------------------

def create_task(
    label: str | None = None,
    parent_task_id: str | None = None,
    fork_step: int | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create a task with 3 empty steps, or fork from a parent at ``fork_step``.

    When forking: copy steps 1..fork_step (done + data), leave the rest pending.
    Pass ``task_id`` to reuse an existing run directory name (e.g. legacy import).
    """
    task_id = task_id or _new_task_id()
    now = _now()
    with _LOCK, get_conn() as c:
        c.execute(
            "INSERT INTO tasks(task_id,parent_task_id,fork_step,label,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (task_id, parent_task_id, fork_step, label, "idle", now, now),
        )
        for n in (1, 2, 3):
            sid = _step_id(task_id, n)
            parent_sid = None
            status = "pending"
            data_json = None
            if parent_task_id and fork_step and n <= int(fork_step):
                row = c.execute(
                    "SELECT step_id, status, data_json FROM steps WHERE task_id=? AND step_num=?",
                    (parent_task_id, n),
                ).fetchone()
                if row:
                    parent_sid = row["step_id"]
                    status = row["status"] if row["status"] == "done" else "pending"
                    data_json = row["data_json"] if status == "done" else None
            c.execute(
                "INSERT INTO steps(step_id,task_id,step_num,step_type,status,data_json,"
                "parent_step_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, task_id, n, STEP_TYPES[n], status, data_json, parent_sid, now, now),
            )
        c.execute(
            "INSERT INTO audit_log(task_id,step_num,action,detail,status,ts) VALUES(?,?,?,?,?,?)",
            (
                task_id,
                fork_step,
                "fork" if parent_task_id else "create",
                (
                    f"forked from {parent_task_id} at step {fork_step}"
                    if parent_task_id
                    else "new task"
                ),
                "ok",
                now,
            ),
        )
    return get_task(task_id)  # type: ignore[return-value]


def set_task_status(task_id: str, status: str) -> None:
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    with _LOCK, get_conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
            (status, _now(), task_id),
        )


def activate_task(task_id: str) -> dict[str, Any] | None:
    """Mark a task as the one the operator opened.

    If status was ``completed``, flip to ``idle``. Does not change ``running``.
    """
    with _LOCK, get_conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        if row["status"] == "completed":
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("idle", _now(), task_id),
            )
            c.execute(
                "INSERT INTO audit_log(task_id,step_num,action,detail,status,ts) VALUES(?,?,?,?,?,?)",
                (task_id, None, "activate", "completed → idle (opened in UI)", "ok", _now()),
            )
    return get_task(task_id)


def mark_task_running(task_id: str) -> None:
    set_task_status(task_id, "running")


def mark_task_completed_if_ready(task_id: str) -> None:
    """Set task to completed when step 3 is done."""
    with _LOCK, get_conn() as c:
        row = c.execute(
            "SELECT status FROM steps WHERE task_id=? AND step_num=3",
            (task_id,),
        ).fetchone()
        if row and row["status"] == "done":
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("completed", _now(), task_id),
            )


def touch_task(task_id: str) -> None:
    with _LOCK, get_conn() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (_now(), task_id))


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT task_id, parent_task_id, fork_step, label, status, created_at, updated_at "
            "FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            steps = c.execute(
                "SELECT step_num, step_type, status FROM steps WHERE task_id=? ORDER BY step_num",
                (r["task_id"],),
            ).fetchall()
            item["steps"] = [dict(s) for s in steps]
            # Light counts for audit trail
            s1 = c.execute(
                "SELECT data_json FROM steps WHERE task_id=? AND step_num=1", (r["task_id"],)
            ).fetchone()
            s2 = c.execute(
                "SELECT data_json FROM steps WHERE task_id=? AND step_num=2", (r["task_id"],)
            ).fetchone()
            item["kw_count"] = 0
            item["topic_count"] = 0
            if s1 and s1["data_json"]:
                try:
                    kw = json.loads(s1["data_json"])
                    item["kw_count"] = len(kw) if isinstance(kw, list) else 0
                except json.JSONDecodeError:
                    pass
            if s2 and s2["data_json"]:
                try:
                    doc = json.loads(s2["data_json"])
                    topics = doc.get("topics") if isinstance(doc, dict) else doc
                    item["topic_count"] = len(topics) if isinstance(topics, list) else 0
                except json.JSONDecodeError:
                    pass
            out.append(item)
        return out


def get_task(task_id: str) -> dict[str, Any] | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not r:
            return None
        out = dict(r)
        out["steps"] = [
            dict(x)
            for x in c.execute(
                "SELECT step_id, task_id, step_num, step_type, status, parent_step_id, "
                "agent_run_id, created_at, updated_at, "
                "CASE WHEN data_json IS NULL THEN 0 ELSE length(data_json) END AS data_bytes "
                "FROM steps WHERE task_id=? ORDER BY step_num",
                (task_id,),
            ).fetchall()
        ]
        out["audit_log"] = [
            dict(x)
            for x in c.execute(
                "SELECT * FROM audit_log WHERE task_id=? ORDER BY id DESC LIMIT 50",
                (task_id,),
            ).fetchall()
        ]
        return out


# ---- steps ------------------------------------------------------------------

def get_step(task_id: str, step_num: int) -> dict[str, Any] | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM steps WHERE task_id=? AND step_num=?",
            (task_id, step_num),
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        if out.get("data_json"):
            try:
                parsed = json.loads(out["data_json"])
            except json.JSONDecodeError:
                out["data"] = None
            else:
                # Defensive: unwrap double-encoded data (agent passed a JSON
                # string that got json.dumps'd again). Repeat until we get a
                # non-string or hit a depth limit.
                depth = 0
                while isinstance(parsed, str) and depth < 3:
                    s = parsed.strip()
                    if not s or s[0] not in "[{":
                        break
                    try:
                        parsed = json.loads(s)
                    except json.JSONDecodeError:
                        # Try fixing unquoted JS-object-literal keys.
                        import re
                        fixed = re.sub(r'([{,]\s*)([a-zA-Z_]\w*)(\s*:)', r'\1"\2"\3', s)
                        if fixed != s:
                            try:
                                parsed = json.loads(fixed)
                                continue
                            except json.JSONDecodeError:
                                pass
                        break
                    depth += 1
                out["data"] = parsed
        else:
            out["data"] = None
        del out["data_json"]
        return out


def get_step_data(task_id: str, step_num: int) -> Any:
    step = get_step(task_id, step_num)
    if not step:
        return None
    return step.get("data")


def save_step_data(
    task_id: str,
    step_num: int,
    data: Any,
    *,
    status: str = "done",
    agent_run_id: str | None = None,
) -> dict[str, Any]:
    """Persist step payload and optionally mark task running/completed."""
    if step_num not in STEP_TYPES:
        raise ValueError(f"invalid step_num: {step_num}")
    if status not in STEP_STATUSES:
        raise ValueError(f"invalid step status: {status}")
    payload = json.dumps(data, ensure_ascii=False) if data is not None else None
    now = _now()
    with _LOCK, get_conn() as c:
        exists = c.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not exists:
            raise KeyError(f"task not found: {task_id}")
        c.execute(
            "UPDATE steps SET data_json=?, status=?, agent_run_id=COALESCE(?, agent_run_id), "
            "updated_at=? WHERE task_id=? AND step_num=?",
            (payload, status, agent_run_id, now, task_id, step_num),
        )
        c.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (now, task_id))
        c.execute(
            "INSERT INTO audit_log(task_id,step_num,action,detail,status,ts) VALUES(?,?,?,?,?,?)",
            (task_id, step_num, "save_step", f"status={status}", status, now),
        )
        if status == "running":
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("running", now, task_id),
            )
        elif status == "done" and step_num == 3:
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("completed", now, task_id),
            )
        elif status == "done":
            # Step finished but task may still be mid-pipeline — leave running if was running,
            # otherwise idle.
            cur = c.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if cur and cur["status"] == "running":
                # Check if any other step still running
                other = c.execute(
                    "SELECT 1 FROM steps WHERE task_id=? AND status='running'",
                    (task_id,),
                ).fetchone()
                if not other:
                    c.execute(
                        "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                        ("idle", now, task_id),
                    )
        elif status == "error":
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("idle", now, task_id),
            )
    return get_step(task_id, step_num)  # type: ignore[return-value]


def set_step_status(
    task_id: str,
    step_num: int,
    status: str,
    *,
    agent_run_id: str | None = None,
) -> None:
    if status not in STEP_STATUSES:
        raise ValueError(f"invalid step status: {status}")
    now = _now()
    with _LOCK, get_conn() as c:
        c.execute(
            "UPDATE steps SET status=?, agent_run_id=COALESCE(?, agent_run_id), updated_at=? "
            "WHERE task_id=? AND step_num=?",
            (status, agent_run_id, now, task_id, step_num),
        )
        if status == "running":
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("running", now, task_id),
            )
        elif status == "done" and step_num == 3:
            c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                ("completed", now, task_id),
            )
        elif status in ("done", "error", "pending"):
            other = c.execute(
                "SELECT 1 FROM steps WHERE task_id=? AND status='running'",
                (task_id,),
            ).fetchone()
            if not other:
                cur = c.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if cur and cur["status"] == "running":
                    # If step 3 done → completed; else idle
                    s3 = c.execute(
                        "SELECT status FROM steps WHERE task_id=? AND step_num=3",
                        (task_id,),
                    ).fetchone()
                    new_status = "completed" if (s3 and s3["status"] == "done") else "idle"
                    c.execute(
                        "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                        (new_status, now, task_id),
                    )


# ---- agent progress ---------------------------------------------------------

def clear_progress(task_id: str, step_num: int) -> None:
    with _LOCK, get_conn() as c:
        c.execute(
            "DELETE FROM agent_progress WHERE task_id=? AND step_num=?",
            (task_id, step_num),
        )


def append_progress(
    task_id: str,
    step_num: int,
    *,
    step: str,
    task_desc: str,
    conclusion: str = "",
    status: str = "running",
) -> int:
    with _LOCK, get_conn() as c:
        row = c.execute(
            "SELECT COALESCE(MAX(idx), -1) + 1 AS next_idx FROM agent_progress "
            "WHERE task_id=? AND step_num=?",
            (task_id, step_num),
        ).fetchone()
        idx = int(row["next_idx"] if row else 0)
        c.execute(
            "INSERT INTO agent_progress(task_id,step_num,idx,ts,step,task_desc,conclusion,status) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (task_id, step_num, idx, _now(), step, task_desc, conclusion, status),
        )
    return idx


def list_progress(task_id: str, step_num: int, since: int = 0) -> dict[str, Any]:
    with get_conn() as c:
        rows = c.execute(
            "SELECT idx, ts, step, task_desc, conclusion, status FROM agent_progress "
            "WHERE task_id=? AND step_num=? AND idx>=? ORDER BY idx",
            (task_id, step_num, since),
        ).fetchall()
        total = c.execute(
            "SELECT COUNT(*) FROM agent_progress WHERE task_id=? AND step_num=?",
            (task_id, step_num),
        ).fetchone()[0]
        lines = []
        for r in rows:
            lines.append(
                {
                    "idx": r["idx"],
                    "ts": r["ts"],
                    "step": r["step"],
                    "task": r["task_desc"],
                    "conclusion": r["conclusion"] or "",
                    "status": r["status"] or "running",
                }
            )
        return {"ok": True, "lines": lines, "total": total}


# ---- audit ------------------------------------------------------------------

def record_audit(
    task_id: str,
    action: str = "",
    detail: str = "",
    status: str = "",
    step_num: int | None = None,
    returncode: int | None = None,
    error: str = "",
    **_extra: Any,
) -> None:
    """Record an audit event. Accepts both new and legacy call shapes.

    New:  record_audit(task_id, action, detail, status, step_num)
    Old:  record_audit(rid, step, action, detail, status, returncode, error)
    """
    # Detect legacy positional: action looks like a step name and detail looks like an action
    # Old signature: (rid, step, action, detail, status, returncode, error)
    # When called as record_audit(rid, "script", kind, cmd, status, rc, err):
    #   action="script", detail=kind — that's fine for new shape too if we map.
    if returncode is not None or error:
        detail = (detail or "") + (f" rc={returncode}" if returncode is not None else "") + (f" {error}" if error else "")
    # If step_num is a string (legacy mistype), ignore
    if isinstance(step_num, str):
        step_num = None
    with _LOCK, get_conn() as c:
        # Ensure task row exists for legacy run ids
        exists = c.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not exists:
            now = _now()
            c.execute(
                "INSERT OR IGNORE INTO tasks(task_id,status,created_at,updated_at) VALUES(?,?,?,?)",
                (task_id, "idle", now, now),
            )
        c.execute(
            "INSERT INTO audit_log(task_id,step_num,action,detail,status,ts) VALUES(?,?,?,?,?,?)",
            (task_id, step_num, action, detail, status, _now()),
        )
    try:
        touch_task(task_id)
    except Exception:
        pass


def stats() -> dict[str, Any]:
    with get_conn() as c:
        return {
            "tasks": c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            "steps": c.execute("SELECT COUNT(*) FROM steps").fetchone()[0],
            "idle": c.execute("SELECT COUNT(*) FROM tasks WHERE status='idle'").fetchone()[0],
            "running": c.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0],
            "completed": c.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='completed'"
            ).fetchone()[0],
            "audit_events": c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            "db_path": str(_db_path()),
        }


# ---- legacy run import ------------------------------------------------------

_PROGRESS_STEP_NUM = {
    "keywords": 1,
    "discover": 1,
    "enrich": 1,
    "brainstorm": 2,
    "serp": 3,
    "outline": 3,
    "section": 3,
    "faq": 3,
    "meta": 3,
    "placements": 3,
}


def _read_json_file(path: Path) -> Any | None:
    if not path.exists() or path.stat().st_size < 2:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _normalize_keywords(data: Any) -> list[dict[str, Any]] | None:
    if isinstance(data, list) and data:
        return data
    if isinstance(data, dict):
        kw = data.get("keywords")
        if isinstance(kw, list) and kw:
            return kw
    return None


def _normalize_topics(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        topics = data.get("topics")
        if isinstance(topics, list) and topics:
            return data
    if isinstance(data, list) and data:
        return {"version": "1.0", "topics": data}
    return None


def _legacy_run_label(task_id: str) -> str | None:
    with get_conn() as c:
        try:
            row = c.execute(
                "SELECT label FROM runs WHERE id=?",
                (task_id,),
            ).fetchone()
            if row and row["label"]:
                return str(row["label"])
        except sqlite3.OperationalError:
            pass
    return None


def _import_agent_progress(run_dir: Path, task_id: str) -> int:
    path = run_dir / "agent-progress.jsonl"
    if not path.exists():
        return 0
    imported = 0
    with _LOCK, get_conn() as c:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            step_name = str(obj.get("step") or "")
            step_num = _PROGRESS_STEP_NUM.get(step_name, 2)
            idx = int(obj.get("idx", imported))
            c.execute(
                "INSERT OR IGNORE INTO agent_progress"
                "(task_id,step_num,idx,ts,step,task_desc,conclusion,status) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    step_num,
                    idx,
                    str(obj.get("ts") or _now()),
                    step_name,
                    str(obj.get("task") or ""),
                    str(obj.get("conclusion") or ""),
                    str(obj.get("status") or "running"),
                ),
            )
            imported += 1
    return imported


def import_run_from_disk(
    run_dir: Path,
    *,
    task_id: str | None = None,
    label: str | None = None,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Import one legacy run directory into tasks/steps.

    Uses the run folder name as ``task_id`` so shadow paths stay aligned with
    ``SEO_RUNS_DIR/{task_id}/``.
    """
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise ValueError(f"not a directory: {run_dir}")

    tid = task_id or run_dir.name
    with get_conn() as c:
        exists = c.execute("SELECT 1 FROM tasks WHERE task_id=?", (tid,)).fetchone()
    if exists and skip_existing:
        return {"ok": True, "skipped": True, "task_id": tid, "reason": "already imported"}

    lbl = label
    if not lbl:
        label_path = run_dir / ".label"
        if label_path.exists():
            lbl = label_path.read_text(encoding="utf-8").strip() or None
    if not lbl:
        lbl = _legacy_run_label(tid)
    if not lbl:
        lbl = tid

    kw_data = _normalize_keywords(_read_json_file(run_dir / "kw.json"))
    if kw_data is None:
        kw_data = _normalize_keywords(_read_json_file(run_dir / "kw.raw.json"))
    topics_data = _normalize_topics(_read_json_file(run_dir / "topics.json"))
    article_data = _read_json_file(run_dir / "article-state.json")
    if not isinstance(article_data, dict):
        article_data = None

    has_any = bool(kw_data or topics_data or article_data)
    if not has_any:
        return {"ok": False, "task_id": tid, "reason": "no importable step data"}

    if exists and not skip_existing:
        with _LOCK, get_conn() as c:
            c.execute("DELETE FROM agent_progress WHERE task_id=?", (tid,))
            c.execute("DELETE FROM audit_log WHERE task_id=?", (tid,))
            c.execute("DELETE FROM steps WHERE task_id=?", (tid,))
            c.execute("DELETE FROM tasks WHERE task_id=?", (tid,))

    create_task(label=lbl, task_id=tid)

    agent_run_id = None
    marker = run_dir / ".agent_run_id"
    if marker.exists():
        agent_run_id = marker.read_text(encoding="utf-8").strip() or None

    if kw_data:
        save_step_data(tid, 1, kw_data, status="done")
    if topics_data:
        save_step_data(tid, 2, topics_data, status="done", agent_run_id=agent_run_id)
    if article_data:
        save_step_data(tid, 3, article_data, status="done", agent_run_id=agent_run_id)

    progress_lines = _import_agent_progress(run_dir, tid)
    record_audit(tid, "import", f"from disk {run_dir.name}", "ok", None)

    task = get_task(tid)
    return {
        "ok": True,
        "skipped": False,
        "task_id": tid,
        "label": lbl,
        "kw_count": len(kw_data) if kw_data else 0,
        "topic_count": len(topics_data.get("topics", [])) if topics_data else 0,
        "has_article": bool(article_data),
        "progress_lines": progress_lines,
        "status": (task or {}).get("status"),
    }


def import_runs_from_disk(
    runs_dir: Path,
    *,
    skip_existing: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    """Scan ``runs_dir`` and import every ``run-*`` folder with step data."""
    runs_dir = Path(runs_dir).resolve()
    results: list[dict[str, Any]] = []
    if not runs_dir.is_dir():
        return {"ok": False, "imported": 0, "results": [], "error": f"not a directory: {runs_dir}"}

    dirs = sorted(
        (
            p
            for p in runs_dir.iterdir()
            if p.is_dir()
            and not p.name.startswith(("_", "."))
            and (p.name.startswith("run-") or p.name.startswith("task-"))
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    imported = 0
    skipped = 0
    failed = 0
    for d in dirs:
        try:
            row = import_run_from_disk(d, skip_existing=skip_existing)
            results.append(row)
            if row.get("skipped"):
                skipped += 1
            elif row.get("ok"):
                imported += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            results.append({"ok": False, "task_id": d.name, "error": str(e)})

    return {
        "ok": True,
        "runs_dir": str(runs_dir),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


def reset_all_tasks(keep_keywords: bool = True) -> dict[str, Any]:
    """Wipe all tasks/steps/audit/progress, optionally preserving the keyword pool.

    When ``keep_keywords`` is True, the largest step-1 keyword set across all
    tasks is captured first, all task rows are deleted, then a single fresh
    task is created with those keywords in step 1. Returns the new task info.
    """
    captured_kw: list[dict[str, Any]] | None = None
    with get_conn() as c:
        if keep_keywords:
            rows = c.execute(
                "SELECT task_id, length(data_json) AS sz FROM steps "
                "WHERE step_num=1 AND data_json IS NOT NULL "
                "ORDER BY length(data_json) DESC"
            ).fetchall()
            for r in rows:
                try:
                    doc = json.loads(
                        c.execute(
                            "SELECT data_json FROM steps WHERE task_id=? AND step_num=1",
                            (r["task_id"],),
                        ).fetchone()["data_json"]
                    )
                except (json.JSONDecodeError, TypeError):
                    continue
                kw = doc if isinstance(doc, list) else (
                    doc.get("keywords") if isinstance(doc, dict) else None
                )
                if isinstance(kw, list) and kw:
                    captured_kw = kw
                    break

    with _LOCK, get_conn() as c:
        c.execute("DELETE FROM agent_progress")
        c.execute("DELETE FROM audit_log")
        c.execute("DELETE FROM steps")
        c.execute("DELETE FROM tasks")

    new_task = create_task(label="关键词池" if keep_keywords else None)
    tid = new_task["task_id"]
    if keep_keywords and captured_kw:
        save_step_data(tid, 1, captured_kw, status="done")
        record_audit(tid, "reset", "kept keyword pool", "ok", 1)
    else:
        record_audit(tid, "reset", "wiped all tasks", "ok", None)
    return {"ok": True, "task_id": tid, "kw_count": len(captured_kw or []), "task": get_task(tid)}


# ---- legacy compatibility shims (old server.py callers) ---------------------

def record_run(rid: str, path: str, label: str | None = None, parent_id: str | None = None) -> None:
    """Legacy: map old run-dir create to a task (no-op if task already exists)."""
    with _LOCK, get_conn() as c:
        exists = c.execute("SELECT 1 FROM tasks WHERE task_id=?", (rid,)).fetchone()
        if exists:
            return
    # Create with the given id only if it looks like a task id; otherwise ignore path mapping
    try:
        create_task(label=label, parent_task_id=parent_id)
    except Exception:
        pass


def touch_run(rid: str, status: str | None = None) -> None:
    if status == "active":
        status = "idle"
    if status in TASK_STATUSES:
        try:
            set_task_status(rid, status)
        except Exception:
            pass
    else:
        try:
            touch_task(rid)
        except Exception:
            pass


def record_keywords(rid: str, keywords: list[dict[str, Any]]) -> None:
    try:
        save_step_data(rid, 1, keywords, status="done")
    except KeyError:
        pass


def record_topics(rid: str, topics: list[dict[str, Any]]) -> None:
    try:
        save_step_data(rid, 2, {"version": "1.0", "topics": topics}, status="done")
    except KeyError:
        pass


def record_article_state(rid: str, state: dict[str, Any]) -> None:
    try:
        save_step_data(rid, 3, state, status="done")
    except KeyError:
        pass


def record_agent_run(rid: str, gateway_run_id: str, step: str, status: str = "requested") -> None:
    step_num = {"brainstorm": 2, "serp": 3, "outline": 3, "section": 3, "faq": 3, "meta": 3}.get(
        step, 3
    )
    try:
        set_step_status(rid, step_num, "running", agent_run_id=gateway_run_id)
        record_audit(rid, f"agent:{step}", f"gateway_run={gateway_run_id}", status, step_num)
    except Exception:
        pass


def record_generation_rules(rid: str, rules_doc: dict[str, Any]) -> None:
    record_audit(rid, "generation_rules", "saved", "ok", 3)


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """Legacy alias — returns tasks shaped for old /api/history consumers."""
    tasks = list_tasks(limit)
    return [
        {
            "id": t["task_id"],
            "label": t.get("label"),
            "status": t.get("status"),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at"),
            "phase": None,
            "word_count": None,
            "validation_passed": None,
            "validation_total": None,
        }
        for t in tasks
    ]


def run_detail(rid: str) -> dict[str, Any] | None:
    return get_task(rid)


# Initialize on import.
init_db()
