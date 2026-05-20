"""Conversation Audit Layer (CAL) — Python data-access helpers.

Both Hermes skills and the bridge HTTP API import this module. Skills
call the `write_*` helpers in a fire-and-forget manner; the HTTP API
calls the `read_*` helpers to serve the external Web console.

Failure policy
--------------
Per design, CAL writes are best-effort. The write helpers DO NOT raise
on DB failure — they log and return a sentinel value. The reconcile
loop is responsible for back-filling anything that was dropped. This
keeps the agent main loop resilient to disk full / locked DB / etc.

The READ helpers DO raise so the HTTP API can return a meaningful
status to the Web client.

Concurrency
-----------
SQLite WAL mode, one connection per call via a small `_connect` helper.
We do not pool — writes are short and reads use WAL snapshots. If
profiling shows contention we can switch to a long-lived connection
in a worker thread later.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from .schema import INDEXES, SCHEMA_VERSION, TABLES

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths / connection
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(os.path.expanduser("~/.hermes/kol-ops-bridge/cal.db"))
_DB_PATH_OVERRIDE: Optional[Path] = None
_INIT_LOCK = threading.Lock()
_INIT_DONE: set[str] = set()


def db_path() -> Path:
    """Return the active CAL DB path.

    Override via ``set_db_path(...)`` (tests) or the
    ``HERMES_KOL_OPS_CAL_DB`` env var.
    """
    if _DB_PATH_OVERRIDE is not None:
        return _DB_PATH_OVERRIDE
    env = os.environ.get("HERMES_KOL_OPS_CAL_DB")
    if env:
        return Path(env)
    return _DEFAULT_DB_PATH


def set_db_path(path: Optional[Path]) -> None:
    """Test hook: override the DB path. Pass ``None`` to reset."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path
    _INIT_DONE.clear()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    init_key = str(path)
    if init_key not in _INIT_DONE:
        with _INIT_LOCK:
            if init_key not in _INIT_DONE:
                _init_schema(path)
                _INIT_DONE.add(init_key)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _init_schema(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        for ddl in TABLES.values():
            conn.execute(ddl)
        for idx in INDEXES:
            conn.execute(idx)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal safe-write helper
# ---------------------------------------------------------------------------


def _safe_write(label: str, fn, *args, **kwargs):
    """Run ``fn`` swallowing exceptions per the failure-policy contract.

    Returns the function result on success, ``None`` on failure. Errors
    are logged at WARNING level so they show up in skill traces without
    aborting the agent.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — intentional swallow.
        log.warning("[CAL] %s failed: %s", label, exc)
        return None


# ---------------------------------------------------------------------------
# Identity + alias resolution
# ---------------------------------------------------------------------------


def upsert_identity(
    *,
    handle: str,
    platform: str = "instagram",
    primary_email: Optional[str] = None,
    display_name: Optional[str] = None,
    region: Optional[str] = None,
    creator_type: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    """Insert or update a KOL identity; return its id (best-effort)."""

    def _do():
        with _connect() as conn:
            now = _now_iso()
            cur = conn.execute(
                "SELECT id FROM kol_identity WHERE platform=? AND handle=? AND env=?",
                (platform, handle, env),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    """UPDATE kol_identity
                       SET primary_email = COALESCE(?, primary_email),
                           display_name  = COALESCE(?, display_name),
                           region        = COALESCE(?, region),
                           creator_type  = COALESCE(?, creator_type),
                           updated_at    = ?
                       WHERE id = ?""",
                    (primary_email, display_name, region, creator_type, now, row["id"]),
                )
                return row["id"]
            conn.execute(
                """INSERT INTO kol_identity
                   (handle, platform, primary_email, display_name, region,
                    creator_type, env, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (handle, platform, primary_email, display_name, region,
                 creator_type, env, now, now),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write("upsert_identity", _do)


def add_alias(
    *,
    kol_identity_id: int,
    kind: str,
    value: str,
    source: str = "dispatcher",
    env: str = "LIVE",
) -> Optional[int]:
    """Record an alias (kind, value) → identity. Idempotent."""

    def _do():
        with _connect() as conn:
            now = _now_iso()
            cur = conn.execute(
                "SELECT id, kol_identity_id FROM kol_identity_alias WHERE kind=? AND value=? AND env=?",
                (kind, value, env),
            )
            row = cur.fetchone()
            if row:
                if row["kol_identity_id"] != kol_identity_id:
                    # alias collision — leave the existing pointer, but log.
                    log.warning(
                        "[CAL] alias collision kind=%s value=%s existing=%s new=%s",
                        kind, value, row["kol_identity_id"], kol_identity_id,
                    )
                    return row["id"]
                conn.execute(
                    "UPDATE kol_identity_alias SET last_seen_at=? WHERE id=?",
                    (now, row["id"]),
                )
                return row["id"]
            conn.execute(
                """INSERT INTO kol_identity_alias
                   (kol_identity_id, kind, value, source, first_seen_at, last_seen_at, env)
                   VALUES (?,?,?,?,?,?,?)""",
                (kol_identity_id, kind, value, source, now, now, env),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write("add_alias", _do)


def resolve_identity(
    *,
    aliases: Iterable[tuple[str, str]],
    env: str = "LIVE",
) -> Optional[int]:
    """Look up a KOL identity by trying multiple (kind, value) aliases.

    Returns the first matching identity id, or None.
    Strategy ordering is the caller's responsibility — pass aliases in
    descending confidence order.
    """
    try:
        with _connect() as conn:
            for kind, value in aliases:
                if not value:
                    continue
                row = conn.execute(
                    "SELECT kol_identity_id FROM kol_identity_alias WHERE kind=? AND value=? AND env=?",
                    (kind, value, env),
                ).fetchone()
                if row:
                    return row["kol_identity_id"]
    except Exception as exc:
        log.warning("[CAL] resolve_identity failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def record_event(
    *,
    kol_identity_id: int,
    event_type: str,
    actor: str,
    card_id: Optional[str] = None,
    product_sku: Optional[str] = None,
    campaign_id: Optional[str] = None,
    stage: Optional[str] = None,
    sub_status: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do():
        with _connect() as conn:
            conn.execute(
                """INSERT INTO kol_conversation_events
                   (kol_identity_id, card_id, product_sku, campaign_id,
                    event_type, stage, sub_status, actor, ts, payload_json, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kol_identity_id, card_id, product_sku, campaign_id,
                    event_type, stage, sub_status, actor, _now_iso(),
                    json.dumps(payload or {}, ensure_ascii=False),
                    env,
                ),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write(f"record_event:{event_type}", _do)


# ---------------------------------------------------------------------------
# Draft history
# ---------------------------------------------------------------------------


def record_draft(
    *,
    kol_identity_id: int,
    stage: str,
    draft_id: str,
    context_snapshot: dict[str, Any],
    actor: str,
    triggered_by: str,
    card_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    product_sku: Optional[str] = None,
    sub_status: Optional[str] = None,
    gmail_message_id: Optional[str] = None,
    gmail_thread_id: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    """Record a Gmail draft with its full generation rationale.

    The ``context_snapshot`` MUST include at minimum:
    selling_point_group, hit_skus, current_stage, sub_status,
    budget_total, budget_per_kol, absolute_floor, prior_reply_quotes.
    The schema does not enforce this — it's a skill contract.
    """

    def _do():
        body_hash = (
            hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None
        )
        with _connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO kol_draft_history
                   (kol_identity_id, card_id, campaign_id, product_sku,
                    stage, sub_status, draft_id, gmail_message_id,
                    gmail_thread_id, subject, body, body_hash,
                    context_snapshot_json, created_at, sent_at,
                    actor, triggered_by, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kol_identity_id, card_id, campaign_id, product_sku,
                    stage, sub_status, draft_id, gmail_message_id,
                    gmail_thread_id, subject, body, body_hash,
                    json.dumps(context_snapshot, ensure_ascii=False),
                    _now_iso(), None, actor, triggered_by, env,
                ),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write(f"record_draft:{stage}", _do)


def mark_draft_sent(*, draft_id: str, sent_at: Optional[str] = None, env: str = "LIVE") -> None:
    def _do():
        with _connect() as conn:
            conn.execute(
                "UPDATE kol_draft_history SET sent_at=? WHERE draft_id=? AND env=?",
                (sent_at or _now_iso(), draft_id, env),
            )

    _safe_write("mark_draft_sent", _do)


def attach_gmail_ids_to_draft(
    *,
    draft_id: str,
    gmail_message_id: str,
    gmail_thread_id: str,
    env: str = "LIVE",
) -> None:
    """Backfill Gmail IDs on a draft row (used when Gmail push happens
    asynchronously after the CAL row is created)."""

    def _do():
        with _connect() as conn:
            conn.execute(
                """UPDATE kol_draft_history
                   SET gmail_message_id = ?, gmail_thread_id = ?
                   WHERE draft_id = ? AND env = ?""",
                (gmail_message_id, gmail_thread_id, draft_id, env),
            )

    _safe_write("attach_gmail_ids_to_draft", _do)


def find_draft_by_thread_id(
    *,
    gmail_thread_id: str,
    env: str = "LIVE",
) -> Optional[dict[str, Any]]:
    """Look up the most recent draft for a Gmail thread (reply matching)."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT * FROM kol_draft_history
                   WHERE gmail_thread_id = ? AND env = ?
                   ORDER BY id DESC LIMIT 1""",
                (gmail_thread_id, env),
            ).fetchone()
            return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("[CAL] find_draft_by_thread_id failed: %s", exc)
        return None


def find_draft_by_message_id(
    *,
    gmail_message_id: str,
    env: str = "LIVE",
) -> Optional[dict[str, Any]]:
    """Look up a draft by the outbound Gmail message id (In-Reply-To match)."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """SELECT * FROM kol_draft_history
                   WHERE gmail_message_id = ? AND env = ?
                   ORDER BY id DESC LIMIT 1""",
                (gmail_message_id, env),
            ).fetchone()
            return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("[CAL] find_draft_by_message_id failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Reply history
# ---------------------------------------------------------------------------


def record_reply(
    *,
    kol_identity_id: int,
    gmail_message_id: str,
    received_at: str,
    match_strategy: str,
    match_confidence: float,
    card_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    gmail_thread_id: Optional[str] = None,
    from_addr: Optional[str] = None,
    snippet: Optional[str] = None,
    body: Optional[str] = None,
    intent: Optional[str] = None,
    confidence: Optional[float] = None,
    handled_action: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do():
        with _connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO kol_reply_history
                   (kol_identity_id, card_id, campaign_id, gmail_message_id,
                    gmail_thread_id, from_addr, received_at, snippet, body,
                    intent, confidence, match_strategy, match_confidence,
                    handled_action, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kol_identity_id, card_id, campaign_id, gmail_message_id,
                    gmail_thread_id, from_addr, received_at, snippet, body,
                    intent, confidence, match_strategy, match_confidence,
                    handled_action, env,
                ),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write("record_reply", _do)


# ---------------------------------------------------------------------------
# Negotiation history
# ---------------------------------------------------------------------------


def record_negotiation(
    *,
    kol_identity_id: int,
    decision: str,
    decided_at: Optional[str] = None,
    card_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    product_sku: Optional[str] = None,
    kol_request_amount: Optional[float] = None,
    currency: str = "USD",
    agent_counter_amount: Optional[float] = None,
    decision_reason: Optional[str] = None,
    budget_per_kol_at_time: Optional[float] = None,
    absolute_floor_at_time: Optional[float] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do():
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM kol_negotiation_history WHERE kol_identity_id=? AND env=?",
                (kol_identity_id, env),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                """INSERT INTO kol_negotiation_history
                   (kol_identity_id, card_id, campaign_id, product_sku,
                    seq, kol_request_amount, currency, agent_counter_amount,
                    decision, decision_reason, budget_per_kol_at_time,
                    absolute_floor_at_time, decided_at, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kol_identity_id, card_id, campaign_id, product_sku,
                    seq, kol_request_amount, currency, agent_counter_amount,
                    decision, decision_reason, budget_per_kol_at_time,
                    absolute_floor_at_time, decided_at or _now_iso(), env,
                ),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write("record_negotiation", _do)


# ---------------------------------------------------------------------------
# Escalation history
# ---------------------------------------------------------------------------


def record_escalation(
    *,
    reason: str,
    ts: Optional[str] = None,
    kol_identity_id: Optional[int] = None,
    card_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    classifier_confidence: Optional[float] = None,
    ai_recommendation: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do():
        with _connect() as conn:
            conn.execute(
                """INSERT INTO escalation_history
                   (kol_identity_id, card_id, campaign_id, ts, reason,
                    classifier_confidence, ai_recommendation, env)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    kol_identity_id, card_id, campaign_id, ts or _now_iso(),
                    reason, classifier_confidence, ai_recommendation, env,
                ),
            )
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return _safe_write(f"record_escalation:{reason}", _do)


# ---------------------------------------------------------------------------
# Read API (raises on failure)
# ---------------------------------------------------------------------------


def _rows(query: str, args: tuple = ()) -> list[dict[str, Any]]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def list_identities(env: str = "LIVE") -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM kol_identity WHERE env=? ORDER BY updated_at DESC",
        (env,),
    )


def get_identity(identity_id: int) -> Optional[dict[str, Any]]:
    rows = _rows("SELECT * FROM kol_identity WHERE id=?", (identity_id,))
    return rows[0] if rows else None


def list_timeline(identity_id: int, env: str = "LIVE") -> dict[str, list[dict[str, Any]]]:
    return {
        "events": _rows(
            "SELECT * FROM kol_conversation_events WHERE kol_identity_id=? AND env=? ORDER BY ts",
            (identity_id, env),
        ),
        "drafts": _rows(
            "SELECT * FROM kol_draft_history WHERE kol_identity_id=? AND env=? ORDER BY created_at",
            (identity_id, env),
        ),
        "replies": _rows(
            "SELECT * FROM kol_reply_history WHERE kol_identity_id=? AND env=? ORDER BY received_at",
            (identity_id, env),
        ),
        "negotiations": _rows(
            "SELECT * FROM kol_negotiation_history WHERE kol_identity_id=? AND env=? ORDER BY seq",
            (identity_id, env),
        ),
        "escalations": _rows(
            "SELECT * FROM escalation_history WHERE kol_identity_id=? AND env=? ORDER BY ts",
            (identity_id, env),
        ),
        "aliases": _rows(
            "SELECT * FROM kol_identity_alias WHERE kol_identity_id=? AND env=? ORDER BY first_seen_at",
            (identity_id, env),
        ),
    }


def get_draft(draft_id: str, env: str = "LIVE") -> Optional[dict[str, Any]]:
    rows = _rows(
        "SELECT * FROM kol_draft_history WHERE draft_id=? AND env=?",
        (draft_id, env),
    )
    return rows[0] if rows else None


def list_drafts_pending_review(env: str = "LIVE") -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM kol_draft_history WHERE sent_at IS NULL AND env=? ORDER BY created_at DESC",
        (env,),
    )


def list_escalations_open(env: str = "LIVE") -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM escalation_history WHERE human_decision IS NULL AND env=? ORDER BY ts DESC",
        (env,),
    )


def list_recent_events(limit: int = 100, env: str = "LIVE") -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM kol_conversation_events WHERE env=? ORDER BY id DESC LIMIT ?",
        (env, limit),
    )


def latest_event_id(env: str = "LIVE") -> int:
    rows = _rows(
        "SELECT COALESCE(MAX(id), 0) AS m FROM kol_conversation_events WHERE env=?",
        (env,),
    )
    return int(rows[0]["m"]) if rows else 0


# ---------------------------------------------------------------------------
# TEST data cleanup
# ---------------------------------------------------------------------------


def wipe_env(env: str) -> dict[str, int]:
    """Delete all rows for a given ``env`` value.

    Returns a dict of {table: rows_deleted}. Safety-fenced to TEST/LIVE
    only; anything else raises.
    """
    if env not in {"TEST", "LIVE"}:
        raise ValueError(f"refusing to wipe unknown env: {env!r}")
    out: dict[str, int] = {}
    with _connect() as conn:
        for table in (
            "escalation_history",
            "kol_negotiation_history",
            "kol_reply_history",
            "kol_draft_history",
            "kol_conversation_events",
            "kol_identity_alias",
            "kol_identity",
        ):
            cur = conn.execute(f"DELETE FROM {table} WHERE env=?", (env,))
            out[table] = cur.rowcount
        conn.commit()
    return out
