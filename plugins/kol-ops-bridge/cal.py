"""Conversation Audit Layer (CAL) — v2 data-access helpers.

Goal-driven schema (see schema.py v2). Public surface used by:

* Hermes skills — fire-and-forget writes via ``write_facts`` /
  ``write_event`` / ``open_escalation``;
* Plugin HTTP API (``plugin_api.py``) — both reads and writes;
* CLI tool (``scripts/kol_bridge_tool.py``) — same surface as HTTP.

Failure policy
--------------
* ``_safe_*`` write helpers swallow exceptions, log at WARNING, and
  return ``None``. The reconcile/router loops are responsible for
  retry / back-fill.
* Read helpers raise on DB error so the API can return a sensible
  status to the Web client.

Concurrency
-----------
SQLite WAL, one connection per call. Heavy read paths (``get_goal_state``)
fall through to a single connection per request — good enough until
profiling shows otherwise.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Iterator, Mapping, Optional

from .goals import GOALS, Context, all_goals
from .schema import FACT_NAMESPACES, GOAL_NAMES, recreate_all

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection / init
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(os.path.expanduser("~/.hermes/kol-ops-bridge/cal.db"))
_DB_PATH_OVERRIDE: Optional[Path] = None
_INIT_LOCK = threading.Lock()
_INIT_DONE: set[str] = set()


def db_path() -> Path:
    if _DB_PATH_OVERRIDE is not None:
        return _DB_PATH_OVERRIDE
    env = os.environ.get("HERMES_KOL_OPS_CAL_DB")
    if env:
        return Path(env)
    return _DEFAULT_DB_PATH


def set_db_path(path: Optional[Path]) -> None:
    """Test hook: override the DB path; pass ``None`` to reset."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path
    _INIT_DONE.clear()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    # sqlite3.Connection's built-in context manager only commits/rolls back;
    # it does NOT close the connection. Without this wrapper, every caller's
    # ``with _connect() as conn:`` leaks two file descriptors (.db, .db-wal),
    # which eventually trips SQLITE_CANTOPEN ("unable to open database file")
    # under sustained load.
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path)
    if key not in _INIT_DONE:
        with _INIT_LOCK:
            if key not in _INIT_DONE:
                _bootstrap(path)
                _INIT_DONE.add(key)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
    finally:
        conn.close()


def _bootstrap(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # Use plain DDL (CREATE IF NOT EXISTS) on first touch; tests/demo
        # call ``hard_reset()`` explicitly when they want a clean slate.
        from .schema import INDEXES, TABLES, VIEWS  # local import avoids cycles
        for ddl in TABLES.values():
            conn.execute(ddl)
        _ensure_column(conn, "campaign_config", "test_mode_to", "TEXT")
        _ensure_column(conn, "campaign_config", "product_display_name", "TEXT")
        for ddl in VIEWS.values():
            conn.execute(ddl)
        for idx in INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def hard_reset() -> None:
    """Drop and re-create all CAL objects. Tests / seeds only."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        recreate_all(conn)
        conn.commit()
    finally:
        conn.close()
    _INIT_DONE.add(str(path))


def _safe(label: str, fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:  # noqa: BLE001
        log.exception("[CAL] %s failed", label)
        return None


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _jl(text: Optional[str], default: Any) -> Any:
    if text in (None, ""):
        return default
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# Identity tier
# ---------------------------------------------------------------------------


def upsert_identity(
    *,
    primary_handle: str,
    platform: str = "instagram",
    primary_email: Optional[str] = None,
    display_name: Optional[str] = None,
    region: Optional[str] = None,
    language: Optional[str] = None,
    contact_role: str = "kol",
    default_shipping_address: Optional[Mapping[str, Any]] = None,
    default_payment_method: Optional[str] = None,
    notes: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    """Insert-or-update a KOL identity. Returns its id."""

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            row = conn.execute(
                "SELECT id FROM kol_identity WHERE platform=? AND primary_handle=? AND env=?",
                (platform, primary_handle, env),
            ).fetchone()
            addr_json = _j(default_shipping_address) if default_shipping_address is not None else None
            if row:
                conn.execute(
                    """UPDATE kol_identity SET
                          primary_email = COALESCE(?, primary_email),
                          display_name  = COALESCE(?, display_name),
                          region        = COALESCE(?, region),
                          language      = COALESCE(?, language),
                          contact_role  = COALESCE(?, contact_role),
                          default_shipping_address = COALESCE(?, default_shipping_address),
                          default_payment_method   = COALESCE(?, default_payment_method),
                          notes         = COALESCE(?, notes),
                          updated_at    = ?
                       WHERE id = ?""",
                    (primary_email, display_name, region, language, contact_role,
                     addr_json, default_payment_method, notes, now, row["id"]),
                )
                return int(row["id"])
            conn.execute(
                """INSERT INTO kol_identity
                   (primary_handle, platform, primary_email, display_name, region,
                    language, contact_role, default_shipping_address,
                    default_payment_method, notes, env, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (primary_handle, platform, primary_email, display_name, region,
                 language, contact_role, addr_json, default_payment_method,
                 notes, env, now, now),
            )
            return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    return _safe("upsert_identity", _do)


def get_identity(identity_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM kol_identity WHERE id=?", (identity_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["alt_handles"] = _jl(out.pop("alt_handles_json", "[]"), [])
    out["default_shipping_address"] = _jl(out.get("default_shipping_address"), None)
    return out


def find_identity_by_handle(primary_handle: str, *, platform: str = "instagram",
                            env: str = "LIVE") -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM kol_identity WHERE platform=? AND primary_handle=? AND env=?",
            (platform, primary_handle, env),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Relationship tier
# ---------------------------------------------------------------------------


def upsert_relationship(
    *,
    identity_id: int,
    last_campaign_id: Optional[str] = None,
    last_outcome: Optional[str] = None,
    preferred_skus: Optional[list[str]] = None,
    preferred_mode: Optional[str] = None,
    avg_delivery_quality: Optional[float] = None,
    avg_revision_rounds: Optional[float] = None,
    increment_collabs: bool = False,
    last_archived_at: Optional[str] = None,
    reputation_score: Optional[float] = None,
) -> Optional[int]:
    def _do() -> int:
        with _connect() as conn:
            now = _now()
            existing = conn.execute(
                "SELECT * FROM kol_relationship WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            skus_json = _j(preferred_skus) if preferred_skus is not None else None
            if existing:
                conn.execute(
                    """UPDATE kol_relationship SET
                         total_collabs        = total_collabs + ?,
                         last_campaign_id     = COALESCE(?, last_campaign_id),
                         last_outcome         = COALESCE(?, last_outcome),
                         reputation_score     = COALESCE(?, reputation_score),
                         preferred_skus_json  = COALESCE(?, preferred_skus_json),
                         preferred_mode       = COALESCE(?, preferred_mode),
                         avg_delivery_quality = COALESCE(?, avg_delivery_quality),
                         avg_revision_rounds  = COALESCE(?, avg_revision_rounds),
                         last_archived_at     = COALESCE(?, last_archived_at),
                         updated_at           = ?
                       WHERE identity_id = ?""",
                    (1 if increment_collabs else 0, last_campaign_id, last_outcome,
                     reputation_score, skus_json, preferred_mode,
                     avg_delivery_quality, avg_revision_rounds,
                     last_archived_at, now, identity_id),
                )
            else:
                conn.execute(
                    """INSERT INTO kol_relationship
                       (identity_id, total_collabs, last_campaign_id, last_outcome,
                        reputation_score, preferred_skus_json, preferred_mode,
                        avg_delivery_quality, avg_revision_rounds,
                        last_archived_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (identity_id, 1 if increment_collabs else 0, last_campaign_id,
                     last_outcome, reputation_score, skus_json or "[]",
                     preferred_mode or "unknown", avg_delivery_quality,
                     avg_revision_rounds, last_archived_at, now),
                )
            return identity_id

    return _safe("upsert_relationship", _do)


def get_relationship(identity_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM kol_relationship WHERE identity_id=?", (identity_id,)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["preferred_skus"] = _jl(out.pop("preferred_skus_json", "[]"), [])
    out["collab_history"] = list_collab_history(identity_id)
    return out


def list_collab_history(identity_id: int) -> list[dict[str, Any]]:
    """Per-campaign archival trail for one KOL, sourced from two paths:

    * ``approval.archival_outcome`` facts (modern archive_collab flow).
    * ``legacy.collab_imported`` events (one-shot legacy import).

    Modern entries take precedence on overlap; the result is sorted
    most-recent-first by archived_at.
    """
    with _connect() as conn:
        modern_rows = conn.execute(
            """SELECT campaign_id, fact_value, captured_at, env
                 FROM kol_facts_latest
                WHERE identity_id=? AND fact_key='approval.archival_outcome'""",
            (identity_id,),
        ).fetchall()
        legacy_rows = conn.execute(
            """SELECT campaign_id, ts, payload_json, env
                 FROM kol_conversation_events
                WHERE identity_id=? AND event_type='legacy.collab_imported'""",
            (identity_id,),
        ).fetchall()

    by_campaign: dict[str, dict[str, Any]] = {}
    for r in modern_rows:
        cid = r["campaign_id"]
        if not cid:
            continue
        outcome = _jl(r["fact_value"], None)
        by_campaign[cid] = {
            "campaign_id": cid,
            "outcome": outcome if isinstance(outcome, str) else "",
            "archived_at": r["captured_at"],
            "notes": None,
            "source": "archive",
            "env": r["env"],
        }
    for r in legacy_rows:
        cid = r["campaign_id"]
        if not cid or cid in by_campaign:
            continue
        payload = _jl(r["payload_json"], {}) or {}
        notes_parts: list[str] = []
        for field in ("notes", "product", "source_section"):
            v = payload.get(field)
            if v:
                notes_parts.append(f"{field}={v}")
        by_campaign[cid] = {
            "campaign_id": cid,
            "outcome": payload.get("outcome") or "",
            "archived_at": r["ts"],
            "notes": " · ".join(notes_parts) or None,
            "source": "legacy_import",
            "env": r["env"],
            "handle": payload.get("handle"),
            "skus": payload.get("skus") or [],
        }
    items = list(by_campaign.values())
    items.sort(key=lambda x: x.get("archived_at") or "", reverse=True)
    return items


def list_archived_kols(
    *,
    env: str = "LIVE",
    q: Optional[str] = None,
    last_outcome: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """List KOL identities with at least one archived collab, joined with
    relationship summary. Supports handle/email substring (``q``), last
    outcome filter, and platform filter. ``env`` scopes the relationship
    rows (env is stored on kol_facts/events, not on relationship; we
    return all KOLs whose relationship has been archived at least once).
    """
    where: list[str] = ["r.total_collabs > 0"]
    args: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        where.append("(i.primary_handle LIKE ? OR i.display_name LIKE ? OR i.primary_email LIKE ?)")
        args += [like, like, like]
    if last_outcome:
        where.append("r.last_outcome = ?")
        args.append(last_outcome)
    if platform:
        where.append("i.platform = ?")
        args.append(platform)
    where_sql = " WHERE " + " AND ".join(where)
    with _connect() as conn:
        total_row = conn.execute(
            f"""SELECT COUNT(*) AS n
                  FROM kol_identity i
                  JOIN kol_relationship r ON r.identity_id = i.id
                  {where_sql}""",
            args,
        ).fetchone()
        rows = conn.execute(
            f"""SELECT i.id              AS identity_id,
                       i.primary_handle  AS primary_handle,
                       i.display_name    AS display_name,
                       i.platform        AS platform,
                       i.primary_email   AS primary_email,
                       r.total_collabs   AS total_collabs,
                       r.last_outcome    AS last_outcome,
                       r.last_campaign_id AS last_campaign_id,
                       r.last_archived_at AS last_archived_at,
                       r.preferred_mode  AS preferred_mode,
                       r.preferred_skus_json AS preferred_skus_json
                  FROM kol_identity i
                  JOIN kol_relationship r ON r.identity_id = i.id
                  {where_sql}
              ORDER BY r.last_archived_at DESC NULLS LAST, r.total_collabs DESC, i.id DESC
                 LIMIT ? OFFSET ?""",
            args + [int(limit), int(offset)],
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["preferred_skus"] = _jl(d.pop("preferred_skus_json", "[]"), [])
        items.append(d)
    return {
        "total": int(total_row["n"]) if total_row else 0,
        "limit": int(limit),
        "offset": int(offset),
        "items": items,
        "env": env,
    }


def get_reusable_facts(identity_id: int) -> dict[str, Any]:
    """Identity-level facts a re-engagement skill can plausibly reuse."""
    ident = get_identity(identity_id) or {}
    rel = get_relationship(identity_id) or {}
    return {
        "default_shipping_address": ident.get("default_shipping_address"),
        "default_payment_method": ident.get("default_payment_method"),
        "preferred_skus": rel.get("preferred_skus", []),
        "preferred_mode": rel.get("preferred_mode", "unknown"),
        "last_outcome": rel.get("last_outcome"),
        "total_collabs": rel.get("total_collabs", 0),
    }


# ---------------------------------------------------------------------------
# Campaign tier
# ---------------------------------------------------------------------------


def upsert_campaign_config(*, campaign_id: str, env: str = "LIVE", **fields: Any) -> Optional[str]:
    """Upsert a campaign_config row. ``fields`` keys map 1:1 to columns;
    list/dict values are JSON-encoded into the matching ``*_json`` column.
    """
    json_cols = {
        "commission_band": "commission_band_json",
        "deliverable_platforms": "deliverable_platforms_json",
        "sku_whitelist": "sku_whitelist_json",
        "followup_intervals": "followup_intervals_json",
    }
    scalar_allowed = {
        "label", "product_display_name", "product_unit_price", "barter_policy",
        "paid_ceiling", "deliverable_count_per_platform", "extra_notes",
        "brief_template_id", "color_variant_policy", "audit_standards_md",
        "test_mode_to", "contract_required", "status",
    }

    def _do() -> str:
        with _connect() as conn:
            now = _now()
            row = conn.execute(
                "SELECT campaign_id FROM campaign_config WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            sets, vals = [], []
            for k, v in fields.items():
                if k in json_cols and v is not None:
                    sets.append(f"{json_cols[k]} = ?")
                    vals.append(_j(v))
                elif k in scalar_allowed and v is not None:
                    if k == "contract_required":
                        v = 1 if v else 0
                    sets.append(f"{k} = ?")
                    vals.append(v)
            if row:
                sets.append("env = ?")
                vals.append(env)
                if sets:
                    sets.append("updated_at = ?")
                    vals.append(now)
                    vals.append(campaign_id)
                    conn.execute(
                        f"UPDATE campaign_config SET {', '.join(sets)} WHERE campaign_id=?",
                        vals,
                    )
            else:
                conn.execute(
                    """INSERT INTO campaign_config
                       (campaign_id, env, created_at, updated_at)
                       VALUES (?,?,?,?)""",
                    (campaign_id, env, now, now),
                )
                if sets:
                    sets.append("updated_at = ?")
                    vals.append(now)
                    vals.append(campaign_id)
                    conn.execute(
                        f"UPDATE campaign_config SET {', '.join(sets)} WHERE campaign_id=?",
                        vals,
                    )
            return campaign_id

    return _safe("upsert_campaign_config", _do)


def list_campaigns(*, env: Optional[str] = None) -> list[dict[str, Any]]:
    """Distinct (campaign_id, env) pairs known to the bridge, with
    candidate counts. Pulls from ``campaign_candidates`` (the source of
    truth for what shows up on the kanban) and left-joins
    ``campaign_config`` for label/status. Sorted newest-first by the
    candidate row's max ``updated_at`` so the most-recently touched
    campaign floats to the top of the picker.
    """
    where = ""
    args: list[Any] = []
    if env is not None:
        where = " WHERE c.env = ?"
        args.append(env)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT c.campaign_id      AS campaign_id,
                       c.env              AS env,
                       COUNT(*)           AS candidate_count,
                       MAX(c.updated_at)  AS last_touched_at,
                       cf.label           AS label,
                       cf.status          AS status
                  FROM campaign_candidates c
             LEFT JOIN campaign_config cf
                    ON cf.campaign_id = c.campaign_id
                {where}
              GROUP BY c.campaign_id, c.env
              ORDER BY MAX(c.updated_at) DESC, c.campaign_id ASC""",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


def get_campaign_config(campaign_id: str, *, env: Optional[str] = None) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        if env is None:
            row = conn.execute(
                "SELECT * FROM campaign_config WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM campaign_config WHERE campaign_id=? AND env=?",
                (campaign_id, env),
            ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["commission_band"] = _jl(out.pop("commission_band_json", "{}"), {})
    out["deliverable_platforms"] = _jl(out.pop("deliverable_platforms_json", "[]"), [])
    out["sku_whitelist"] = _jl(out.pop("sku_whitelist_json", "[]"), [])
    out["followup_intervals"] = _jl(out.pop("followup_intervals_json", "{}"), {})
    out["contract_required"] = bool(out.get("contract_required", 1))
    return out


def upsert_candidate(
    *,
    campaign_id: str,
    identity_id: Optional[int],
    source: str,
    discovery_score: Optional[float] = None,
    relationship_status: str = "new_prospect",
    candidate_status: str = "discovered",
    review_reason: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do() -> int:
        with _connect() as conn:
            now = _now()
            existing = conn.execute(
                "SELECT id FROM campaign_candidates WHERE campaign_id=? AND identity_id=? AND env=?",
                (campaign_id, identity_id, env),
            ).fetchone()
            payload_json = _j(payload or {})
            if existing:
                conn.execute(
                    """UPDATE campaign_candidates SET
                          source = ?, discovery_score = COALESCE(?, discovery_score),
                          relationship_status = ?, candidate_status = ?,
                          review_reason = COALESCE(?, review_reason),
                          payload_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (source, discovery_score, relationship_status, candidate_status,
                     review_reason, payload_json, now, existing["id"]),
                )
                return int(existing["id"])
            conn.execute(
                """INSERT INTO campaign_candidates
                   (campaign_id, identity_id, source, discovery_score,
                    relationship_status, candidate_status, review_reason,
                    payload_json, env, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (campaign_id, identity_id, source, discovery_score,
                 relationship_status, candidate_status, review_reason,
                 payload_json, env, now, now),
            )
            return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    return _safe("upsert_candidate", _do)


def list_candidates(campaign_id: str, *, env: str = "LIVE") -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM campaign_candidates WHERE campaign_id=? AND env=? ORDER BY id",
            (campaign_id, env),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = _jl(d.pop("payload_json", "{}"), {})
        out.append(d)
    return out


def set_candidate_status(
    *,
    campaign_id: str,
    identity_ids: Iterable[int],
    candidate_status: str,
    review_reason: Optional[str] = None,
    env: str = "LIVE",
) -> int:
    ids = list(identity_ids)
    if not ids:
        return 0

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            qmarks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"""UPDATE campaign_candidates
                       SET candidate_status=?,
                           review_reason=COALESCE(?, review_reason),
                           updated_at=?
                     WHERE campaign_id=? AND env=? AND identity_id IN ({qmarks})""",
                [candidate_status, review_reason, now, campaign_id, env, *ids],
            )
            return cur.rowcount or 0

    return _safe("set_candidate_status", _do) or 0



def select_candidates_for_outreach(
    *, campaign_id: str, identity_ids: Iterable[int], selected_by: str, env: str = "LIVE"
) -> int:
    ids = list(identity_ids)
    if not ids:
        return 0

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            qmarks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"""UPDATE campaign_candidates
                       SET candidate_status='selected_for_outreach',
                           selected_by=?, selected_at=?, updated_at=?
                     WHERE campaign_id=? AND env=? AND identity_id IN ({qmarks})""",
                [selected_by, now, now, campaign_id, env, *ids],
            )
            # Ensure every selected identity has a kol_goal_state row for
            # this (campaign, env). Without this, an approve that bypasses
            # discovery_router never triggers write_facts → no recompute →
            # get_goal_state returns the default "inactive" for outreach,
            # which blocks every downstream draft skill that gates on
            # goals.outreach.status == "active".
            for ident in ids:
                _recompute_goals_inner(
                    conn, identity_id=int(ident),
                    campaign_id=campaign_id, env=env,
                )
            return cur.rowcount or 0

    return _safe("select_candidates_for_outreach", _do) or 0


def resolve_candidate_relationships(*, campaign_id: str, env: str = "LIVE") -> int:
    """Look up `kol_relationship` for each candidate and set
    ``relationship_status``. Returns rows updated.
    """

    def _do() -> int:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT c.id AS cid, c.identity_id, r.total_collabs, r.last_outcome
                     FROM campaign_candidates c
                     LEFT JOIN kol_relationship r ON r.identity_id = c.identity_id
                    WHERE c.campaign_id=? AND c.env=?""",
                (campaign_id, env),
            ).fetchall()
            now = _now()
            n = 0
            for r in rows:
                if not r["identity_id"]:
                    continue
                total = r["total_collabs"] or 0
                last = r["last_outcome"]
                if total <= 0:
                    status = "new_prospect"
                elif last in ("disputed", "content_failed"):
                    status = "repeat_kol_needs_review"
                else:
                    status = "repeat_kol"
                conn.execute(
                    "UPDATE campaign_candidates SET relationship_status=?, updated_at=? WHERE id=?",
                    (status, now, r["cid"]),
                )
                n += 1
            return n

    return _safe("resolve_candidate_relationships", _do) or 0


# ---------------------------------------------------------------------------
# Facts + goal-state recompute
# ---------------------------------------------------------------------------


class FactNamespaceError(ValueError):
    pass


# Per plan A1: each fact key that "moves the deal" should emit a
# matching per-goal ``event_type`` so the timeline reflects what
# happened. The mapping is the canonical vocabulary the plan lists.
# Keys not in the map are still persisted to ``kol_facts`` — they just
# don't generate a timeline event.
_FACT_EVENT_TYPE_MAP: Final[dict[str, tuple[str, str, str]]] = {
    # fact_key -> (event_type, goal, lane)
    "offer.outreach_sent":                   ("outreach.sent",                 "outreach",                 "commerce"),
    "offer.interest_signal":                 ("interest.signal_received",      "interest_qualification",   "commerce"),
    "offer.sku_locked":                      ("product.sku_locked",            "product_selection",        "commerce"),
    "offer.color_or_variant_locked":         ("product.color_or_variant_locked", "product_selection",      "commerce"),
    "offer.fit_confirmed":                   ("product.fit_confirmed",         "product_selection",        "commerce"),
    "offer.deliverable_platforms":           ("deliverables.platforms_set",    "deliverables_scope",       "commerce"),
    "offer.deliverable_count_per_platform":  ("deliverables.count_set",        "deliverables_scope",       "commerce"),
    "offer.usage_rights_discussed":          ("deliverables.usage_rights",     "deliverables_scope",       "commerce"),
    "offer.kol_paid_quote":                  ("compensation.kol_quoted",       "compensation_negotiation", "commerce"),
    "offer.compensation_mode":               ("compensation.mode_set",         "compensation_negotiation", "commerce"),
    "offer.agreed_terms":                    ("compensation.agreed",           "compensation_negotiation", "commerce"),
    "offer.contract_sent":                   ("contract.sent",                 "contract_signing",         "commerce"),
    "offer.contract_signed":                 ("contract.signed",               "contract_signing",         "commerce"),
    "offer.contract_declined_reason":        ("contract.declined",             "contract_signing",         "commerce"),
    "fulfillment.address_collected":         ("logistics.address_collected",   "logistics",               "fulfillment"),
    "fulfillment.shipping_method":           ("logistics.shipping_method_set", "logistics",               "fulfillment"),
    "fulfillment.tracking_filled":           ("logistics.tracking_filled",     "logistics",               "fulfillment"),
    "fulfillment.delivered_confirmed":       ("logistics.delivered",           "logistics",               "fulfillment"),
    "offer.brief_sent":                      ("content.brief_sent",            "content_production",       "fulfillment"),
    "offer.draft_submitted":                 ("content.draft_submitted",       "content_production",       "fulfillment"),
    "offer.review_verdict":                  ("content.review_verdict",        "content_review_and_golive", "publish"),
    "offer.posted_url":                      ("content.posted",                "content_review_and_golive", "publish"),
    "offer.boost_assets_status":             ("content.boost_assets_requested", "content_review_and_golive", "publish"),
}


# Per-fact-key value-shape validators. Run at write_facts() time so bad
# fact shapes fail fast on the writer (skill or CLI) instead of surfacing
# days later when an operator tries to act on the fact. Adding a new
# validator is a one-line entry — keep the predicates simple and
# fact-specific; this is not a general schema framework. Keys absent from
# this map are written unchanged.
def _validate_approval_reply_draft(value: Any) -> None:
    if not isinstance(value, dict):
        raise FactNamespaceError("approval.reply_draft value must be a dict")
    draft = value.get("draft")
    if not isinstance(draft, dict):
        raise FactNamespaceError("approval.reply_draft must carry a draft object")
    missing = [
        k for k in ("subject", "body", "to")
        if not (isinstance(draft.get(k), str) and draft[k].strip())
    ]
    if missing:
        raise FactNamespaceError(
            f"approval.reply_draft.draft missing/empty: {', '.join(missing)}"
        )


_FACT_SHAPE_VALIDATORS: Final[dict[str, Callable[[Any], None]]] = {
    "approval.reply_draft": _validate_approval_reply_draft,
}


def _truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        return v.strip() not in {"", "false", "False", "0", "null", "none"}
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    return True


def write_facts(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    namespace: str,
    facts: Mapping[str, Any],
    source: str = "skill",
    source_event_id: Optional[int] = None,
    env: str = "LIVE",
) -> Optional[int]:
    """Append a batch of facts under one namespace. Validates the
    ``<namespace>.<key>`` contract and rejects unknown namespaces.

    For fact keys in :data:`_FACT_EVENT_TYPE_MAP` whose value is truthy,
    also emits a matching ``kol_conversation_events`` row so the
    timeline reflects the per-goal vocabulary from plan A1 without
    requiring each skill to call ``write-event`` separately.

    Returns the number of rows inserted.
    """
    if namespace not in FACT_NAMESPACES:
        raise FactNamespaceError(f"unknown namespace: {namespace!r}")
    prefix = f"{namespace}."
    for k, v in facts.items():
        if not k.startswith(prefix):
            raise FactNamespaceError(
                f"fact_key {k!r} must start with {prefix!r}"
            )
        validator = _FACT_SHAPE_VALIDATORS.get(k)
        if validator is not None:
            validator(v)

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            n = 0
            for k, v in facts.items():
                conn.execute(
                    """INSERT INTO kol_facts
                       (identity_id, campaign_id, fact_namespace, fact_key,
                        fact_value, source, source_event_id, captured_at, env)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (identity_id, campaign_id, namespace, k,
                     _j(v) if not isinstance(v, str) else v,
                     source, source_event_id, now, env),
                )
                n += 1
                # Auto-emit per-goal event_type when a meaningful fact
                # transitions from absent/falsy to truthy. Skip for
                # decision-style approvals (those have their own paths).
                mapped = _FACT_EVENT_TYPE_MAP.get(k)
                if (mapped and campaign_id and _truthy(v)):
                    event_type, goal, lane = mapped
                    conn.execute(
                        """INSERT INTO kol_conversation_events
                           (identity_id, campaign_id, event_type, goal, lane,
                            actor, ts, payload_json, env)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (identity_id, campaign_id, event_type, goal, lane,
                         source, now,
                         _j({"fact_key": k, "fact_value": v}), env),
                    )
            # Trigger goal recompute inline (cheap; under 50ms typical).
            if campaign_id:
                _recompute_goals_inner(conn, identity_id=identity_id,
                                       campaign_id=campaign_id, env=env)
            return n

    return _safe("write_facts", _do)


def write_facts_multi(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    namespaces: Mapping[str, Mapping[str, Any]],
    source: str = "skill",
    source_event_id: Optional[int] = None,
    env: str = "LIVE",
) -> dict[str, int]:
    """Write facts across multiple namespaces in one logical operation.

    ``namespaces`` is ``{namespace: {fact_key: value, ...}}``. All namespaces
    are validated up front (atomic-ish: any ``FactNamespaceError`` aborts the
    call before any insert). Each non-empty namespace is forwarded to
    ``write_facts`` (which triggers goal recompute once per call).

    Returns ``{namespace: rows_inserted}``.
    """
    # Pre-validate to avoid partial writes when caller passes an invalid key.
    for ns, facts in namespaces.items():
        if ns not in FACT_NAMESPACES:
            raise FactNamespaceError(f"unknown namespace: {ns!r}")
        prefix = f"{ns}."
        for k, v in facts.items():
            if not k.startswith(prefix):
                raise FactNamespaceError(
                    f"fact_key {k!r} must start with {prefix!r}"
                )
            validator = _FACT_SHAPE_VALIDATORS.get(k)
            if validator is not None:
                validator(v)

    written: dict[str, int] = {}
    for ns, facts in namespaces.items():
        if not facts:
            continue
        n = write_facts(
            identity_id=identity_id, campaign_id=campaign_id,
            namespace=ns, facts=facts,
            source=source, source_event_id=source_event_id, env=env,
        )
        written[ns] = int(n or 0)
    return written


def latest_facts_for(
    *, identity_id: int, campaign_id: Optional[str], env: str = "LIVE"
) -> dict[str, Any]:
    """Return the latest value per fact_key for an (identity, campaign)
    pair, with identity-level facts (campaign_id IS NULL) merged underneath
    so thread-level overrides win.
    """

    def _decode(v: Any) -> Any:
        if not isinstance(v, str):
            return v
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return v

    with _connect() as conn:
        ident_rows = conn.execute(
            """SELECT fact_key, fact_value FROM kol_facts_latest
                WHERE identity_id=? AND campaign_id IS NULL AND env=?""",
            (identity_id, env),
        ).fetchall()
        camp_rows = []
        if campaign_id:
            camp_rows = conn.execute(
                """SELECT fact_key, fact_value FROM kol_facts_latest
                    WHERE identity_id=? AND campaign_id=? AND env=?""",
                (identity_id, campaign_id, env),
            ).fetchall()
    out: dict[str, Any] = {r["fact_key"]: _decode(r["fact_value"]) for r in ident_rows}
    for r in camp_rows:
        out[r["fact_key"]] = _decode(r["fact_value"])
    return out


def recompute_goals(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> int:
    with _connect() as conn:
        return _recompute_goals_inner(conn, identity_id=identity_id,
                                      campaign_id=campaign_id, env=env) or 0


def _recompute_goals_inner(
    conn: sqlite3.Connection, *, identity_id: int, campaign_id: str, env: str
) -> int:
    state = latest_facts_for(identity_id=identity_id, campaign_id=campaign_id, env=env)
    cfg_row = conn.execute(
        "SELECT * FROM campaign_config WHERE campaign_id=?", (campaign_id,)
    ).fetchone()
    cfg: dict[str, Any] = dict(cfg_row) if cfg_row else {}
    if cfg:
        cfg["contract_required"] = bool(cfg.get("contract_required", 1))
        cfg["sku_whitelist"] = _jl(cfg.get("sku_whitelist_json"), [])
        cfg["deliverable_count_per_platform"] = cfg.get("deliverable_count_per_platform")
    rel_row = conn.execute(
        "SELECT * FROM kol_relationship WHERE identity_id=?", (identity_id,)
    ).fetchone()
    rel = dict(rel_row) if rel_row else {}
    is_repeat = (rel.get("total_collabs") or 0) > 0
    ctx = Context(campaign_cfg=cfg, relationship=rel, is_repeat_kol=is_repeat)

    now = _now()
    n = 0
    for goal in all_goals():
        missing = goal.missing(state)
        if goal.is_skipped(state, ctx):
            status = "skipped"
        elif goal.is_satisfied(state):
            status = "satisfied"
        elif goal.can_enter(state, ctx):
            status = "active"
        else:
            status = "inactive"
        conn.execute(
            """INSERT INTO kol_goal_state
               (identity_id, campaign_id, goal, status, lane,
                missing_facts_json, meta_json, updated_at, env)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(identity_id, campaign_id, goal, env) DO UPDATE SET
                  status=excluded.status,
                  lane=excluded.lane,
                  missing_facts_json=excluded.missing_facts_json,
                  updated_at=excluded.updated_at""",
            (identity_id, campaign_id, goal.name, status, goal.lane,
             _j(missing), "{}", now, env),
        )
        n += 1
    return n


def get_goal_state(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT goal, status, lane, missing_facts_json, blocking_escalation_id,
                      updated_at
                 FROM kol_goal_state
                WHERE identity_id=? AND campaign_id=? AND env=?""",
            (identity_id, campaign_id, env),
        ).fetchall()
    by_name = {r["goal"]: r for r in rows}
    out = []
    for name in GOAL_NAMES:
        r = by_name.get(name)
        if not r:
            out.append({"goal": name, "status": "inactive", "lane": GOALS[name].lane,
                        "missing_facts": list(GOALS[name].required_facts)})
            continue
        out.append({
            "goal": r["goal"],
            "status": r["status"],
            "lane": r["lane"],
            "missing_facts": _jl(r["missing_facts_json"], []),
            "blocking_escalation_id": r["blocking_escalation_id"],
            "updated_at": r["updated_at"],
        })
    return out


def get_lanes_view(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> dict[str, list[dict[str, Any]]]:
    state_list = get_goal_state(identity_id=identity_id, campaign_id=campaign_id, env=env)
    out: dict[str, list[dict[str, Any]]] = {"commerce": [], "fulfillment": [], "publish": [], "meta": []}
    for s in state_list:
        out.setdefault(s["lane"], []).append(s)
    return out


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def write_event(
    *,
    identity_id: int,
    event_type: str,
    actor: str,
    campaign_id: Optional[str] = None,
    goal: Optional[str] = None,
    lane: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do() -> int:
        with _connect() as conn:
            now = _now()
            conn.execute(
                """INSERT INTO kol_conversation_events
                   (identity_id, campaign_id, event_type, goal, lane,
                    actor, ts, payload_json, env)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (identity_id, campaign_id, event_type, goal, lane, actor, now,
                 _j(payload or {}), env),
            )
            return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    return _safe("write_event", _do)


def list_events(
    *,
    env: str = "LIVE",
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
    limit: int = 200,
    since_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Read ``kol_conversation_events`` in reverse-chronological order.

    Used by the console's ReplyMonitor + KolDetail.timeline + the cron
    poller's watermark logic.  ``since_id`` lets callers do incremental
    pulls; ``identity_id`` / ``campaign_id`` are optional narrowing
    filters (combinable).  Results are dicts ready for JSON serialization.
    """
    limit = max(1, min(int(limit), 1000))
    where = ["env = ?"]
    args: list[Any] = [env]
    if identity_id is not None:
        where.append("identity_id = ?")
        args.append(int(identity_id))
    if campaign_id is not None:
        where.append("campaign_id = ?")
        args.append(campaign_id)
    if since_id is not None:
        where.append("id > ?")
        args.append(int(since_id))
    sql = (
        "SELECT id, identity_id, campaign_id, event_type, goal, lane, "
        "actor, ts, payload_json, env FROM kol_conversation_events "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    )
    args.append(limit)

    def _do() -> list[dict[str, Any]]:
        with _connect() as conn:
            rows = conn.execute(sql, args).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                payload = d.pop("payload_json", None)
                try:
                    d["payload"] = json.loads(payload) if payload else {}
                except (TypeError, ValueError):
                    d["payload"] = {}
                out.append(d)
            return out

    return _safe("list_events", _do) or []


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


_DEFAULT_MAX_ESCALATION_DEPTH = 3


def _read_max_escalation_depth(conn: sqlite3.Connection) -> int:
    """Best-effort read of ``max_escalation_depth`` from active
    ``policies/escalation_rules`` row. Falls back to default on any
    parse / IO error so escalations never break on a missing policy.
    """
    try:
        from . import policies as _policies  # local import; avoid cycles
    except Exception:  # pragma: no cover — defensive
        return _DEFAULT_MAX_ESCALATION_DEPTH
    try:
        row = _policies.get_policy(conn, scope="escalation_rules")
        if not row or not row.get("content_md"):
            return _DEFAULT_MAX_ESCALATION_DEPTH
        parsed = _policies.parse_escalation_rules(row["content_md"])
        val = parsed.get("top", {}).get("max_escalation_depth")
        if isinstance(val, int) and val >= 1:
            return val
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("read_max_escalation_depth failed: %s", exc)
    return _DEFAULT_MAX_ESCALATION_DEPTH


def open_escalation(
    *,
    identity_id: Optional[int] = None,
    reason: str,
    campaign_id: Optional[str] = None,
    goal: Optional[str] = None,
    severity: str = "normal",
    question_to_operator: Optional[str] = None,
    parent_escalation_id: Optional[int] = None,
    resume_context: Optional[Mapping[str, Any]] = None,
    env: str = "LIVE",
) -> Optional[int]:
    def _do() -> int:
        with _connect() as conn:
            now = _now()
            attempts = 1
            if parent_escalation_id is not None:
                row = conn.execute(
                    "SELECT attempts_count FROM kol_escalations WHERE id=?",
                    (parent_escalation_id,),
                ).fetchone()
                attempts = (row["attempts_count"] if row else 0) + 1
            # Depth-aware hint: when this new escalation already meets
            # the configured depth, tag resume_context so downstream
            # consumers (skill kol-escalation-resumer / web console)
            # surface a "human takeover suggested" badge. We never
            # auto-abort here — operator must explicitly terminate.
            ctx: dict[str, Any] = dict(resume_context or {})
            max_depth = _read_max_escalation_depth(conn)
            if attempts >= max_depth:
                ctx["force_human_takeover_hint"] = True
                ctx.setdefault("max_escalation_depth", max_depth)
                ctx.setdefault("attempts_count", attempts)
            conn.execute(
                """INSERT INTO kol_escalations
                   (identity_id, campaign_id, goal, reason, severity, state,
                    question_to_operator, parent_escalation_id, attempts_count,
                    resume_context_json, created_at, updated_at, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identity_id, campaign_id, goal, reason, severity,
                 "awaiting_answer", question_to_operator, parent_escalation_id,
                 attempts, _j(ctx), now, now, env),
            )
            esc_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            # Phase F state-machine closure: when a child escalation is
            # opened, the parent must transition out of any non-terminal
            # state (``awaiting_answer`` / ``answered`` / ``resuming`` /
            # ``resolved``) into ``re_escalated`` so the parent never
            # silently stays "resolved" while a child is pending. This
            # was the root cause of stuck ``answered`` parents observed
            # in earlier runs.
            if parent_escalation_id is not None:
                conn.execute(
                    """UPDATE kol_escalations
                          SET state='re_escalated', updated_at=?
                        WHERE id=?
                          AND state IN ('awaiting_answer','answered',
                                        'resuming','resolved')""",
                    (now, parent_escalation_id),
                )
            if identity_id and campaign_id and goal:
                conn.execute(
                    """UPDATE kol_goal_state SET status='blocked',
                          blocking_escalation_id=?, updated_at=?
                        WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                    (esc_id, now, identity_id, campaign_id, goal, env),
                )
            return esc_id

    esc_id = _safe("open_escalation", _do)
    if esc_id is not None:
        _notify_escalation_opened(
            esc_id=esc_id,
            identity_id=identity_id,
            campaign_id=campaign_id,
            goal=goal,
            reason=reason,
            severity=severity,
            question=question_to_operator,
        )
    return esc_id


def _notify_escalation_opened(
    *,
    esc_id: int,
    identity_id: Optional[int],
    campaign_id: Optional[str],
    goal: Optional[str],
    reason: str,
    severity: str,
    question: Optional[str],
) -> None:
    """Best-effort DingTalk notification for a fresh escalation.

    Failures are swallowed (notifier itself never raises on transport
    error). We import lazily so the cal module stays usable in test
    environments that stub out notifier."""
    try:
        from . import notifier as _notifier  # local import; avoid cycles
    except Exception:  # pragma: no cover — defensive
        return
    lines = [
        f"**reason**: {reason}",
        f"**severity**: {severity}",
    ]
    if identity_id:
        lines.append(f"**identity_id**: {identity_id}")
    if campaign_id:
        lines.append(f"**campaign**: {campaign_id}")
    if goal:
        lines.append(f"**goal**: {goal}")
    if question:
        lines.append(f"**question**: {question}")
    try:
        _notifier.notify(
            kind="escalation",
            title=f"Escalation #{esc_id} opened",
            lines=lines,
            ref={"escalation_id": esc_id},
        )
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("notifier.notify(escalation) failed: %s", exc)


VALID_ESCALATION_STATES: Final[frozenset[str]] = frozenset({
    "open", "awaiting_answer", "answered", "resuming",
    "resolved", "re_escalated", "aborted",
})


class EscalationStateError(ValueError):
    """Raised when resolve_escalation is called with an unknown final_state."""


def resolve_escalation(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
    operator_facts: Optional[Mapping[str, Any]] = None,
    final_state: str = "resolved",
) -> Optional[int]:
    if final_state not in VALID_ESCALATION_STATES:
        raise EscalationStateError(
            f"unknown final_state {final_state!r}; "
            f"must be one of {sorted(VALID_ESCALATION_STATES)}"
        )

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            conn.execute(
                """UPDATE kol_escalations SET
                       decision=?, decided_by=?, decided_at=?,
                       operator_answer=COALESCE(?, operator_answer),
                       operator_facts_json=COALESCE(?, operator_facts_json),
                       state=?, updated_at=?
                     WHERE id=?""",
                (decision, decided_by, now, operator_answer,
                 _j(operator_facts) if operator_facts else None,
                 final_state, now, escalation_id),
            )
            row = conn.execute(
                "SELECT identity_id, campaign_id, goal, env FROM kol_escalations WHERE id=?",
                (escalation_id,),
            ).fetchone()
            if row and row["identity_id"] and row["campaign_id"] and row["goal"]:
                if final_state == "resolved":
                    conn.execute(
                        """UPDATE kol_goal_state SET status='active',
                              blocking_escalation_id=NULL, updated_at=?
                            WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                        (now, row["identity_id"], row["campaign_id"],
                         row["goal"], row["env"]),
                    )
                elif final_state == "aborted":
                    conn.execute(
                        """UPDATE kol_goal_state SET status='aborted', updated_at=?
                            WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                        (now, row["identity_id"], row["campaign_id"],
                         row["goal"], row["env"]),
                    )
            return escalation_id

    return _safe("resolve_escalation", _do)


def note_rejected_draft(
    *,
    escalation_id: int,
    fact_path: str,
    note: Optional[str],
    decided_by: str,
) -> bool:
    """Append a rejected-draft entry to an escalation's resume_context.

    Used when the operator rejects an ``approval.reply_draft`` linked to
    an open escalation: instead of opening a derived escalation, we
    leave a breadcrumb on the original so the operator (or a later
    agent run) can see what was tried and why it was refused.
    """
    def _do() -> bool:
        with _connect() as conn:
            row = conn.execute(
                "SELECT resume_context_json FROM kol_escalations WHERE id=?",
                (escalation_id,),
            ).fetchone()
            if not row:
                return False
            ctx = _jl(row["resume_context_json"], {}) or {}
            history = list(ctx.get("rejected_drafts") or [])
            history.append({
                "fact_path": fact_path,
                "note": note or "",
                "decided_by": decided_by,
                "decided_at": _now(),
            })
            ctx["rejected_drafts"] = history[-10:]
            conn.execute(
                "UPDATE kol_escalations SET resume_context_json=?, updated_at=? WHERE id=?",
                (_j(ctx), _now(), escalation_id),
            )
            return True

    return bool(_safe("note_rejected_draft", _do))


def get_escalation_campaign_id(escalation_id: int) -> Optional[str]:
    """Return the ``campaign_id`` of an escalation row, or None.

    Used by the bridge HTTP layer to inherit campaign scope when an
    ``approval.*`` fact is written with a ``linked_escalation_id`` but
    no ``campaign_id`` in the body.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT campaign_id FROM kol_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
    if not row:
        return None
    cid = row["campaign_id"]
    return str(cid) if cid else None


def list_escalations(*, state: Optional[str] = None, env: str = "LIVE") -> list[dict[str, Any]]:
    with _connect() as conn:
        if state:
            rows = conn.execute(
                "SELECT * FROM kol_escalations WHERE state=? AND env=? ORDER BY id DESC",
                (state, env),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kol_escalations WHERE env=? ORDER BY id DESC", (env,)
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["resume_context"] = _jl(d.pop("resume_context_json", "{}"), {})
        d["operator_facts"] = _jl(d.pop("operator_facts_json", None), None)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Approvals (read-only view over kol_facts)
# ---------------------------------------------------------------------------


def list_pending_approvals(*, env: str = "LIVE") -> list[dict[str, Any]]:
    """Return latest ``approval.*`` facts that actually need an operator
    decision. A fact is "pending" only when its value is a JSON object
    with no ``decision`` field set yet (or set to ``"pending"``).

    Scalar-valued ``approval.*`` facts (e.g. ``approval.<goal>_terminated
    = true`` from skill 3d, ``approval.next_action_type = "<type>"`` from
    skill 3e) are skill-internal markers consumed by downstream skills,
    not items requiring a console decision, so they are excluded here.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM kol_facts_latest
                WHERE fact_namespace='approval' AND env=?
                ORDER BY id DESC""",
            (env,),
        ).fetchall()
    out = []
    for r in rows:
        val = _jl(r["fact_value"], None)
        if not isinstance(val, dict):
            continue
        decision = val.get("decision")
        if decision in (None, "pending"):
            out.append({
                "identity_id": r["identity_id"],
                "campaign_id": r["campaign_id"],
                "fact_key": r["fact_key"],
                "value": val,
                "captured_at": r["captured_at"],
            })
    return out


def list_decided_approvals(
    *, status: str, env: str = "LIVE"
) -> list[dict[str, Any]]:
    """Return decided ``approval.*`` facts whose ``value.decision``
    matches ``status``. ``status`` must be one of ``approved`` /
    ``rejected`` / ``all`` (``all`` returns both approved and rejected,
    not pending — pending is served by ``list_pending_approvals``).
    """
    if status not in ("approved", "rejected", "all"):
        raise ValueError(f"unknown status: {status!r}")
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM kol_facts_latest
                WHERE fact_namespace='approval' AND env=?
                ORDER BY id DESC""",
            (env,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        val = _jl(r["fact_value"], None)
        if not isinstance(val, dict):
            continue
        decision = val.get("decision")
        if status == "all":
            if decision not in ("approved", "rejected"):
                continue
        elif decision != status:
            continue
        out.append({
            "identity_id": r["identity_id"],
            "campaign_id": r["campaign_id"],
            "fact_key": r["fact_key"],
            "value": val,
            "captured_at": r["captured_at"],
        })
    return out


def list_approved_reply_drafts(*, env: str = "LIVE") -> list[dict[str, Any]]:
    """Return approved ``approval.reply_draft`` facts not yet marked sent."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM kol_facts_latest
                WHERE fact_namespace='approval'
                  AND fact_key='approval.reply_draft'
                  AND env=?
                ORDER BY id DESC""",
            (env,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        identity_id = int(r["identity_id"])
        campaign_id = r["campaign_id"]
        facts = latest_facts_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env
        )
        if facts.get("offer.outreach_sent") is True:
            continue
        value = _jl(r["fact_value"], None)
        if not isinstance(value, dict) or value.get("decision") != "approved":
            continue
        gmail_draft = value.get("gmail_draft")
        if not isinstance(gmail_draft, dict) or not gmail_draft.get("thread_id"):
            continue
        out.append({
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "fact_key": r["fact_key"],
            "value": value,
            "gmail_draft": gmail_draft,
            "captured_at": r["captured_at"],
        })
    return out


# ---------------------------------------------------------------------------
# Archive helper
# ---------------------------------------------------------------------------


def archive_collab(
    *,
    identity_id: int,
    campaign_id: str,
    outcome: str,
    preferred_skus: Optional[list[str]] = None,
    preferred_mode: Optional[str] = None,
    avg_revision_rounds: Optional[float] = None,
    delivery_quality: Optional[float] = None,
    decided_by: str = "skill:archival-writer",
) -> Optional[int]:
    """Push thread-level archival facts into identity-level relationship,
    and write an ``approval.archival_outcome`` fact tying it to the
    archival goal's required_facts.
    """
    now = _now()
    upsert_relationship(
        identity_id=identity_id,
        last_campaign_id=campaign_id,
        last_outcome=outcome,
        preferred_skus=preferred_skus,
        preferred_mode=preferred_mode,
        avg_delivery_quality=delivery_quality,
        avg_revision_rounds=avg_revision_rounds,
        increment_collabs=True,
        last_archived_at=now,
    )
    write_facts(
        identity_id=identity_id,
        campaign_id=campaign_id,
        namespace="approval",
        facts={
            "approval.archival_outcome": outcome,
            "approval.relationship_synced": True,
            "approval.preferred_skus_synced": True,
            "approval.preferred_mode_synced": True,
            "approval.followups_pending": False,
        },
        source=decided_by,
    )
    return identity_id


# ---------------------------------------------------------------------------
# Stuck-goal scanner (cron-callable)
# ---------------------------------------------------------------------------


_DEFAULT_FOLLOWUP_HOURS = 72


def check_stuck_goals(*, env: str = "LIVE", now: Optional[str] = None) -> list[dict[str, Any]]:
    """Scan ``kol_goal_state`` for goals whose ``updated_at`` is older
    than the campaign's ``followup_intervals[goal]`` (hours; defaults to
    72h). For each stuck goal, emit a best-effort DingTalk notification
    and return the matched rows.

    Designed to be called by a cron job (HTTP or CLI). Notifier failures
    are swallowed; the function itself never raises on transport error.
    """
    import datetime as _dt
    now_iso = now or _now()
    try:
        now_dt = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        return []

    with _connect() as conn:
        rows = conn.execute(
            """SELECT identity_id, campaign_id, goal, lane, status, updated_at
                 FROM kol_goal_state
                WHERE status IN ('active', 'blocked') AND env=?""",
            (env,),
        ).fetchall()

    # Cache campaign_config followup_intervals lookups.
    intervals_cache: dict[str, dict[str, Any]] = {}
    stuck: list[dict[str, Any]] = []
    for r in rows:
        cid = r["campaign_id"]
        if cid not in intervals_cache:
            cfg = get_campaign_config(cid) or {}
            intervals_cache[cid] = cfg.get("followup_intervals") or {}
        interval_hours = intervals_cache[cid].get(r["goal"], _DEFAULT_FOLLOWUP_HOURS)
        try:
            updated = _dt.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        age_hours = (now_dt - updated).total_seconds() / 3600.0
        if age_hours < float(interval_hours):
            continue
        rec = {
            "identity_id": r["identity_id"],
            "campaign_id": cid,
            "goal": r["goal"],
            "lane": r["lane"],
            "status": r["status"],
            "age_hours": round(age_hours, 1),
            "threshold_hours": interval_hours,
        }
        stuck.append(rec)
        _notify_goal_stuck(rec)
    return stuck


def _notify_goal_stuck(rec: Mapping[str, Any]) -> None:
    try:
        from . import notifier as _notifier  # local import; avoid cycles
    except Exception:  # pragma: no cover
        return
    lines = [
        f"**campaign**: {rec.get('campaign_id')}",
        f"**identity_id**: {rec.get('identity_id')}",
        f"**goal**: {rec.get('goal')} ({rec.get('lane')})",
        f"**status**: {rec.get('status')}",
        f"**age**: {rec.get('age_hours')}h (threshold {rec.get('threshold_hours')}h)",
    ]
    try:
        _notifier.notify(
            kind="info",
            title=f"Goal stuck: {rec.get('goal')}",
            lines=lines,
            ref={
                "identity_id": rec.get("identity_id"),
                "campaign_id": rec.get("campaign_id"),
            },
        )
    except Exception as exc:  # pragma: no cover
        log.warning("notifier.notify(goal_stuck) failed: %s", exc)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "FactNamespaceError",
    "archive_collab",
    "check_stuck_goals",
    "db_path",
    "find_identity_by_handle",
    "get_campaign_config",
    "get_escalation_campaign_id",
    "get_goal_state",
    "get_identity",
    "get_lanes_view",
    "get_relationship",
    "get_reusable_facts",
    "hard_reset",
    "latest_facts_for",
    "list_campaigns",
    "list_candidates",
    "list_escalations",
    "list_events",
    "list_pending_approvals",
    "list_decided_approvals",
    "list_approved_reply_drafts",
    "open_escalation",
    "recompute_goals",
    "resolve_candidate_relationships",
    "resolve_escalation",
    "select_candidates_for_outreach",
    "set_db_path",
    "upsert_campaign_config",
    "upsert_candidate",
    "upsert_identity",
    "upsert_relationship",
    "write_event",
    "write_facts",
]
