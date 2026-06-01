"""Append-only jsonl buffer + replay for confirmed-candidate ingest.

Event-sourced buffer: each line is an immutable event. Replay scans events
to determine pending/failed/imported state per ``fact_id``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from . import confirmed_ingest  # type: ignore[import-not-found]

BufferStatus = Literal["pending", "imported", "failed"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_buffer_path() -> Path:
    root = Path(
        os.environ.get(
            "HERMES_KOL_OPS_INGEST_STATE_DIR",
            os.path.expanduser("~/.hermes/kol-ops-bridge"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root / "confirmed_ingest_buffer.jsonl"


def _fsync_file(f) -> None:
    f.flush()
    os.fsync(f.fileno())


def append_enqueue(
    *,
    path: Path,
    campaign_id: str,
    env: str,
    payload: dict[str, Any],
    fact_id: Optional[str] = None,
    identity_hint: Optional[str] = None,
) -> dict[str, Any]:
    """Append an enqueue event. Returns the event record."""
    fid = fact_id or str(uuid.uuid4())
    handle = identity_hint
    if not handle:
        ident = payload.get("identity") or {}
        if isinstance(ident, dict):
            handle = ident.get("primary_handle")
    event: dict[str, Any] = {
        "event": "enqueue",
        "fact_id": fid,
        "campaign_id": campaign_id,
        "env": env,
        "identity_hint": handle,
        "payload": payload,
        "buffered_at": _now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _fsync_file(fh)
    return event


def append_status(
    *,
    path: Path,
    fact_id: str,
    status: BufferStatus,
    error: Optional[str] = None,
) -> None:
    event: dict[str, Any] = {
        "event": status,
        "fact_id": fact_id,
        "at": _now_iso(),
    }
    if error:
        event["error"] = error
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        _fsync_file(fh)


def iter_events(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build_buffer_state(path: Path) -> dict[str, dict[str, Any]]:
    """Return ``fact_id -> {status, enqueue, retry_count, last_error}``."""
    state: dict[str, dict[str, Any]] = {}
    for ev in iter_events(path):
        et = ev.get("event")
        fid = ev.get("fact_id")
        if not isinstance(fid, str):
            continue
        row = state.setdefault(
            fid,
            {"status": "pending", "enqueue": None, "retry_count": 0, "last_error": None},
        )
        if et == "enqueue":
            row["enqueue"] = ev
            row["status"] = "pending"
        elif et == "imported":
            row["status"] = "imported"
            row["last_error"] = None
        elif et == "failed":
            row["status"] = "failed"
            row["retry_count"] = int(row.get("retry_count") or 0) + 1
            row["last_error"] = ev.get("error")
    return state


def list_pending(path: Path) -> list[dict[str, Any]]:
    state = build_buffer_state(path)
    out: list[dict[str, Any]] = []
    for fid, row in state.items():
        if row.get("status") in ("pending", "failed") and row.get("enqueue"):
            out.append({"fact_id": fid, **row})
    return out


def replay_pending(
    *,
    path: Optional[Path] = None,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Replay pending/failed enqueue events through ``ingest_confirmed_candidate``."""
    buf = path or default_buffer_path()
    pending = list_pending(buf)
    if limit is not None:
        pending = pending[:limit]

    imported: list[str] = []
    failed: list[dict[str, Any]] = []
    skipped_already: list[str] = []

    for row in pending:
        fid = row["fact_id"]
        enqueue = row.get("enqueue") or {}
        campaign_id = enqueue.get("campaign_id")
        env = enqueue.get("env")
        payload = enqueue.get("payload") or {}
        if not isinstance(campaign_id, str) or not isinstance(env, str):
            append_status(path=buf, fact_id=fid, status="failed", error="invalid enqueue event")
            failed.append({"fact_id": fid, "error": "invalid enqueue event"})
            continue
        try:
            result = confirmed_ingest.ingest_confirmed_candidate(
                campaign_id=campaign_id,
                env=env,
                source=payload.get("source") or "buffer:replay",
                identity=payload.get("identity") or {},
                candidate=payload.get("candidate") or {},
                identity_facts=payload.get("identity_facts"),
                ingest_id=fid,
            )
            if result.get("already_imported"):
                skipped_already.append(fid)
            else:
                imported.append(fid)
            append_status(path=buf, fact_id=fid, status="imported")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            append_status(path=buf, fact_id=fid, status="failed", error=msg)
            failed.append({"fact_id": fid, "error": msg})

    return {
        "buffer_path": str(buf),
        "attempted": len(pending),
        "imported": imported,
        "skipped_already_imported": skipped_already,
        "failed": failed,
    }
