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

import datetime as _dt
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

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


def _connect() -> sqlite3.Connection:
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
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _bootstrap(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # Use plain DDL (CREATE IF NOT EXISTS) on first touch; tests/demo
        # call ``hard_reset()`` explicitly when they want a clean slate.
        from .schema import INDEXES, TABLES, VIEWS  # local import avoids cycles
        for ddl in TABLES.values():
            conn.execute(ddl)
        for ddl in VIEWS.values():
            conn.execute(ddl)
        for idx in INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()


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
    except Exception as exc:  # noqa: BLE001
        log.warning("[CAL] %s failed: %s", label, exc)
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
    return out


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
        "label", "product_unit_price", "barter_policy", "paid_ceiling",
        "deliverable_count_per_platform", "extra_notes", "brief_template_id",
        "color_variant_policy", "audit_standards_md", "contract_required",
        "status",
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


def get_campaign_config(campaign_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_config WHERE campaign_id=?", (campaign_id,)
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

    Returns the number of rows inserted.
    """
    if namespace not in FACT_NAMESPACES:
        raise FactNamespaceError(f"unknown namespace: {namespace!r}")
    prefix = f"{namespace}."
    for k in facts:
        if not k.startswith(prefix):
            raise FactNamespaceError(
                f"fact_key {k!r} must start with {prefix!r}"
            )

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
            # Trigger goal recompute inline (cheap; under 50ms typical).
            if campaign_id:
                _recompute_goals_inner(conn, identity_id=identity_id,
                                       campaign_id=campaign_id, env=env)
            return n

    return _safe("write_facts", _do)


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


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


def open_escalation(
    *,
    identity_id: Optional[int],
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
            conn.execute(
                """INSERT INTO kol_escalations
                   (identity_id, campaign_id, goal, reason, severity, state,
                    question_to_operator, parent_escalation_id, attempts_count,
                    resume_context_json, created_at, updated_at, env)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identity_id, campaign_id, goal, reason, severity,
                 "awaiting_answer", question_to_operator, parent_escalation_id,
                 attempts, _j(resume_context or {}), now, now, env),
            )
            esc_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            if identity_id and campaign_id and goal:
                conn.execute(
                    """UPDATE kol_goal_state SET status='blocked',
                          blocking_escalation_id=?, updated_at=?
                        WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                    (esc_id, now, identity_id, campaign_id, goal, env),
                )
            return esc_id

    return _safe("open_escalation", _do)


def resolve_escalation(
    *,
    escalation_id: int,
    decision: str,
    decided_by: str,
    operator_answer: Optional[str] = None,
    operator_facts: Optional[Mapping[str, Any]] = None,
    final_state: str = "resolved",
) -> Optional[int]:
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
    """Return latest ``approval.*`` facts whose value has no
    ``decision`` field set yet. Heuristic; UI uses this as queue.
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
        decision = None
        if isinstance(val, dict):
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
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "FactNamespaceError",
    "archive_collab",
    "db_path",
    "find_identity_by_handle",
    "get_campaign_config",
    "get_goal_state",
    "get_identity",
    "get_lanes_view",
    "get_relationship",
    "get_reusable_facts",
    "hard_reset",
    "latest_facts_for",
    "list_candidates",
    "list_escalations",
    "list_pending_approvals",
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
