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
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Iterator, Mapping, Optional, Sequence
from urllib.parse import urlparse

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_primary_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if not _EMAIL_RE.match(s):
        raise ValueError(
            f"primary_email must look like 'x@y.tld'; got {value!r}"
        )
    return s.lower()

from .campaign_nox_integration import (
    encode_nox_integration,
    flatten_nox_into_config,
    merge_nox_integration,
    parse_nox_integration_json,
    pick_nox_fields,
)
from .goals import GOALS, Context, all_goals
from . import outreach_touch
from . import reply_draft
from . import reply_chase
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
        _ensure_column(conn, "campaign_config", "product_url", "TEXT")
        _ensure_column(
            conn, "campaign_config", "variant_candidates_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        _ensure_column(conn, "campaign_config", "paid_target_budget", "REAL")
        _ensure_column(conn, "campaign_config", "paid_ratio_override", "REAL")
        _ensure_column(conn, "campaign_config", "nox_integration_json", "TEXT")
        _ensure_column(conn, "kol_relationship", "negotiation_style", "TEXT")
        _ensure_column(conn, "policy_documents", "env", "TEXT")
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
    default_payment_method: Optional[Mapping[str, Any]] = None,
    notes: Optional[str] = None,
    env: str = "LIVE",
) -> Optional[int]:
    """Insert-or-update a KOL identity. Returns its id."""

    primary_email = _normalize_primary_email(primary_email)

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            row = conn.execute(
                "SELECT id FROM kol_identity WHERE platform=? AND primary_handle=? AND env=?",
                (platform, primary_handle, env),
            ).fetchone()
            addr_json = _j(default_shipping_address) if default_shipping_address is not None else None
            pm_json = _j(default_payment_method) if default_payment_method is not None else None
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
                     addr_json, pm_json, notes, now, row["id"]),
                )
                return int(row["id"])
            conn.execute(
                """INSERT INTO kol_identity
                   (primary_handle, platform, primary_email, display_name, region,
                    language, contact_role, default_shipping_address,
                    default_payment_method, notes, env, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (primary_handle, platform, primary_email, display_name, region,
                 language, contact_role, addr_json, pm_json,
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
    out["default_payment_method"] = _jl(out.get("default_payment_method"), None)
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
    negotiation_style: Optional[str] = None,
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
                         negotiation_style    = COALESCE(?, negotiation_style),
                         last_archived_at     = COALESCE(?, last_archived_at),
                         updated_at           = ?
                       WHERE identity_id = ?""",
                    (1 if increment_collabs else 0, last_campaign_id, last_outcome,
                     reputation_score, skus_json, preferred_mode,
                     avg_delivery_quality, avg_revision_rounds,
                     negotiation_style, last_archived_at, now, identity_id),
                )
            else:
                conn.execute(
                    """INSERT INTO kol_relationship
                       (identity_id, total_collabs, last_campaign_id, last_outcome,
                        reputation_score, preferred_skus_json, preferred_mode,
                        avg_delivery_quality, avg_revision_rounds,
                        negotiation_style, last_archived_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (identity_id, 1 if increment_collabs else 0, last_campaign_id,
                     last_outcome, reputation_score, skus_json or "[]",
                     preferred_mode or "unknown", avg_delivery_quality,
                     avg_revision_rounds, negotiation_style, last_archived_at, now),
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


_OUTREACH_TOUCH_EVENT_TYPES: Final[tuple[str, ...]] = (
    "outreach.sent",
    "outbound_draft_created",
)

_LEGACY_COLLAB_IMPORTED: Final[str] = "legacy.collab_imported"
_LEGACY_CAMPAIGN_PREFIX: Final[str] = "legacy-redlist-"

KOL_REGISTRY_FACT_KEYS: Final[tuple[str, ...]] = (
    "identity.followers",
    "identity.follower_count",
    "identity.nox_followers",
    "identity.social_links",
    "identity.primary_email_from_legacy",
    "identity.legacy_import_id",
    "identity.nox_avg_views",
    "identity.avg_views",
    "offer.sku_locked",
    "offer.legacy_product_text",
    "identity.nox_top_region",
    "identity.region",
    "identity.nox_country",
    "identity.nox_gender_skew",
    "identity.nox_audience_age_distribution",
    "identity.nox_audience_adults_split",
    "identity.nox_audience_languages_top",
    "identity.nox_audience_types_top",
    "identity.nox_audience_authenticity",
    "identity.nox_audience_authenticity_range",
    "identity.nox_audience_quality_score",
    "identity.nox_audience_positive_pct",
    "identity.nox_audience_promo_attractiveness",
    "identity.nox_audience_promo_interested_pct",
    "identity.nox_audience_promo_professionalism",
    "identity.nox_audience_interests_top",
    "identity.veedcrawl_profile_followers",
    "identity.veedcrawl_recent_reels_stats",
    "identity.veedcrawl_cache_month",
    "identity.veedcrawl_fetched_at",
)


_SPU_TOUCH_FACT_KEYS: Final[tuple[str, ...]] = (
    "offer.proposed_skus",
    "offer.sku_locked",
    "offer.legacy_product_text",
)


def _first_nonempty_sku(value: Any) -> str | None:
    """Extract the first real SKU/SPU from a fact value (skip booleans)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, list):
        for item in value:
            text = str(item).strip()
            if text and text.lower() not in ("true", "false", "null"):
                return text
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in ("true", "false", "null"):
            return None
        try:
            return _first_nonempty_sku(json.loads(s))
        except (TypeError, ValueError, json.JSONDecodeError):
            return s
    text = str(value).strip()
    return text or None


def _spu_from_campaign_id(campaign_id: str | None) -> str | None:
    """Best-effort SKU prefix from ``SKU-YYYYMMDD`` campaign ids."""
    if not campaign_id:
        return None
    cid = campaign_id.strip()
    match = re.match(r"^([^-]+)-(\d{8})$", cid)
    if match:
        return match.group(1)
    return None


def _resolve_touch_spu(
    facts: Mapping[str, Any],
    campaign_id: str | None,
) -> str:
    """Stable dedupe key: one count per SPU per identity."""
    for key in ("offer.proposed_skus", "offer.sku_locked"):
        spu = _first_nonempty_sku(facts.get(key))
        if spu:
            return spu.upper()
    legacy = facts.get("offer.legacy_product_text")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip().upper()
    from_campaign = _spu_from_campaign_id(campaign_id)
    if from_campaign:
        return from_campaign.upper()
    return f"cid:{campaign_id or 'unknown'}"


def _batch_campaign_offer_facts(
    conn: Any,
    pairs: Iterable[tuple[int, str]],
    *,
    env: str,
) -> dict[tuple[int, str], dict[str, Any]]:
    """Offer facts scoped to (identity_id, campaign_id) touch pairs."""
    unique = sorted({(int(i), str(c)) for i, c in pairs if c})
    if not unique:
        return {}
    key_ph = ",".join("?" * len(_SPU_TOUCH_FACT_KEYS))
    pair_sql = " OR ".join("(identity_id=? AND campaign_id=?)" for _ in unique)
    flat: list[Any] = [env]
    for iid, cid in unique:
        flat.extend([iid, cid])
    flat.extend(_SPU_TOUCH_FACT_KEYS)
    rows = conn.execute(
        f"""SELECT identity_id, campaign_id, fact_key, fact_value
              FROM kol_facts_latest
             WHERE env=?
               AND ({pair_sql})
               AND fact_key IN ({key_ph})""",
        flat,
    ).fetchall()
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for r in rows:
        pair = (int(r["identity_id"]), str(r["campaign_id"]))
        out.setdefault(pair, {})[r["fact_key"]] = _decode_fact_value(r["fact_value"])
    return out


def _batch_outreach_touch_counts(
    conn: Any,
    identity_ids: list[int],
    *,
    env: str,
) -> dict[int, int]:
    """Count distinct SPUs touched (sent mail or draft) per identity.

    Registry callers must pass results through
    ``prior_touch_allowlist.gate_internal_touch_count`` before exposing
    ``internal_touch_count`` to operators.
    """
    if not identity_ids:
        return {}
    id_ph = ",".join("?" * len(identity_ids))
    et_ph = ",".join("?" * len(_OUTREACH_TOUCH_EVENT_TYPES))
    event_rows = conn.execute(
        f"""SELECT DISTINCT identity_id, campaign_id
              FROM kol_conversation_events
             WHERE env=? AND identity_id IN ({id_ph})
               AND event_type IN ({et_ph})""",
        (env, *identity_ids, *_OUTREACH_TOUCH_EVENT_TYPES),
    ).fetchall()
    if not event_rows:
        return {}
    pairs = [(int(r["identity_id"]), str(r["campaign_id"] or "")) for r in event_rows]
    facts_by_pair = _batch_campaign_offer_facts(conn, pairs, env=env)
    spus_by_identity: dict[int, set[str]] = {}
    for iid, cid in pairs:
        facts = facts_by_pair.get((iid, cid), {})
        spu_key = _resolve_touch_spu(facts, cid or None)
        spus_by_identity.setdefault(iid, set()).add(spu_key)
    return {iid: len(spus) for iid, spus in spus_by_identity.items()}


def _registry_fact_is_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0", "", "null"):
            return False
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        decoded = value
    return decoded is True


def _batch_registry_pipeline_flags(
    conn: Any,
    identity_ids: list[int],
    *,
    env: str,
) -> dict[int, dict[str, bool]]:
    """Per-identity outreach pipeline flags for the metrics registry table."""
    if not identity_ids:
        return {}
    id_ph = ",".join("?" * len(identity_ids))
    out: dict[int, dict[str, bool]] = {
        iid: {"has_initial_outreach_draft": False, "has_inbound_reply": False}
        for iid in identity_ids
    }
    for row in conn.execute(
        f"""SELECT identity_id,
                   MAX(CASE
                         WHEN event_type = 'kol_initial_outreach_draft_ready' THEN 1
                         WHEN event_type = 'outbound_draft_created' AND goal = 'outreach' THEN 1
                         ELSE 0
                       END) AS has_initial_draft,
                   MAX(CASE WHEN event_type = 'kol_inbound_reply' THEN 1 ELSE 0 END)
                       AS has_reply
              FROM kol_conversation_events
             WHERE env=? AND identity_id IN ({id_ph})
          GROUP BY identity_id""",
        (env, *identity_ids),
    ):
        iid = int(row["identity_id"])
        out[iid] = {
            "has_initial_outreach_draft": bool(row["has_initial_draft"]),
            "has_inbound_reply": bool(row["has_reply"]),
        }
    for row in conn.execute(
        f"""SELECT identity_id, fact_value
              FROM kol_facts_latest
             WHERE env=? AND identity_id IN ({id_ph})
               AND fact_key = 'offer.outreach_draft_created'""",
        (env, *identity_ids),
    ):
        if _registry_fact_is_true(row["fact_value"]):
            iid = int(row["identity_id"])
            out[iid]["has_initial_outreach_draft"] = True
    return out


def _pick_instagram_url(
    facts: Mapping[str, Any],
    *,
    platform: Any,
    handle: Any,
) -> str | None:
    """Prefer stored social links (legacy import), else guess from handle."""
    links = facts.get("identity.social_links")
    if isinstance(links, list):
        for raw in links:
            if not isinstance(raw, str):
                continue
            url = raw.strip()
            if "instagram.com" in url.lower():
                return url
    return _guess_profile_url(platform, handle)


def _resolve_registry_email(
    primary_email: Any,
    facts: Mapping[str, Any],
) -> str | None:
    if isinstance(primary_email, str) and primary_email.strip():
        return primary_email.strip()
    legacy = facts.get("identity.primary_email_from_legacy")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    return None


def _resolve_registry_followers(facts: Mapping[str, Any]) -> Any:
    for key in (
        "identity.nox_followers",
        "identity.followers",
        "identity.follower_count",
    ):
        value = facts.get(key)
        if value is not None and value != "":
            return value
    return None


def _resolve_registry_avg_views(facts: Mapping[str, Any]) -> Any:
    for key in ("identity.nox_avg_views", "identity.avg_views"):
        value = facts.get(key)
        if value is not None and value != "":
            return value
    return None


def _resolve_registry_target_spu(facts: Mapping[str, Any]) -> str | None:
    """Target SPU from offer facts (legacy import or modern lock)."""
    locked = facts.get("offer.sku_locked")
    if isinstance(locked, list):
        for item in locked:
            text = str(item).strip()
            if text:
                return text
    if isinstance(locked, str) and locked.strip():
        return locked.strip()
    legacy_text = facts.get("offer.legacy_product_text")
    if isinstance(legacy_text, str) and legacy_text.strip():
        return legacy_text.strip()
    return None


def _batch_registry_facts(
    conn: Any,
    identity_ids: list[int],
    *,
    env: str,
    fact_keys: Iterable[str],
) -> dict[int, dict[str, Any]]:
    """Merge identity-level + most-recent campaign facts per key (legacy-safe)."""
    keys = [str(k) for k in fact_keys if k]
    if not identity_ids or not keys:
        return {i: {} for i in identity_ids}
    id_ph = ",".join("?" * len(identity_ids))
    key_ph = ",".join("?" * len(keys))
    out: dict[int, dict[str, Any]] = {i: {} for i in identity_ids}

    ident_rows = conn.execute(
        f"""SELECT identity_id, fact_key, fact_value
              FROM kol_facts_latest
             WHERE env=? AND campaign_id IS NULL
               AND identity_id IN ({id_ph})
               AND fact_key IN ({key_ph})""",
        (env, *identity_ids, *keys),
    ).fetchall()
    for r in ident_rows:
        out[int(r["identity_id"])][r["fact_key"]] = _decode_fact_value(r["fact_value"])

    camp_rows = conn.execute(
        f"""SELECT identity_id, fact_key, fact_value, captured_at, id
              FROM kol_facts_latest
             WHERE env=? AND campaign_id IS NOT NULL
               AND identity_id IN ({id_ph})
               AND fact_key IN ({key_ph})
          ORDER BY identity_id, fact_key, captured_at DESC, id DESC""",
        (env, *identity_ids, *keys),
    ).fetchall()
    seen: set[tuple[int, str]] = set()
    for r in camp_rows:
        pair = (int(r["identity_id"]), str(r["fact_key"]))
        if pair in seen:
            continue
        seen.add(pair)
        out[pair[0]][pair[1]] = _decode_fact_value(r["fact_value"])
    return out


def _registry_search_clause(q: Optional[str]) -> tuple[str, list[Any]]:
    if not q or not q.strip():
        return "", []
    like = f"%{q.strip()}%"
    return (
        " AND (LOWER(i.primary_handle) LIKE LOWER(?) "
        "OR LOWER(i.display_name) LIKE LOWER(?) "
        "OR LOWER(i.primary_email) LIKE LOWER(?))",
        [like, like, like],
    )


def _registry_pool_cte() -> str:
    """Shared CTE: Agent discovery candidates only (``campaign_candidates``)."""
    return """
        WITH pool AS (
            SELECT c.identity_id AS identity_id,
                   c.created_at AS seen_at,
                   c.campaign_id AS campaign_id
              FROM campaign_candidates c
             WHERE c.env = ? AND c.identity_id IS NOT NULL
        ),
        agg AS (
            SELECT identity_id,
                   MIN(seen_at) AS first_discovered_at,
                   MAX(seen_at) AS last_seen_at,
                   0 AS has_legacy_import,
                   1 AS has_discovery
              FROM pool
             GROUP BY identity_id
        ),
        ranked_camp AS (
            SELECT p.identity_id,
                   p.campaign_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.identity_id
                       ORDER BY p.seen_at DESC
                   ) AS rn
              FROM pool p
        )
    """


def _registry_source_clause(source: Optional[str]) -> tuple[str, list[Any]]:
    norm = (source or "all").strip().lower()
    if norm == "legacy":
        # Legacy imports are excluded from the registry pool.
        return " AND 1 = 0", []
    return "", []


def _registry_order_clause(
    sort: Optional[str] = None,
    order: Optional[str] = None,
) -> str:
    """SQL ``ORDER BY`` for registry list (default: ingested_at desc)."""
    sort_norm = (sort or "ingested_at").strip().lower()
    order_norm = (order or "desc").strip().lower()
    direction = "ASC" if order_norm == "asc" else "DESC"
    tie_break = "ASC" if direction == "DESC" else "DESC"
    if sort_norm in ("ingested_at", "first_discovered_at", "created_at"):
        return (
            f" ORDER BY a.first_discovered_at {direction}, "
            f"a.identity_id {tie_break}"
        )
    return f" ORDER BY a.first_discovered_at DESC, a.identity_id DESC"


def list_discovered_kol_registry(
    *,
    env: str = "LIVE",
    q: Optional[str] = None,
    source: Optional[str] = None,
    sort: Optional[str] = None,
    order: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated registry of KOLs discovered via Agent campaigns.

    Pool = ``campaign_candidates`` only (one row per ``identity_id``).
    Legacy red-list imports are intentionally excluded from this view.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    search_sql, search_args = _registry_search_clause(q)
    source_sql, source_args = _registry_source_clause(source)
    order_sql = _registry_order_clause(sort, order)
    pool_cte = _registry_pool_cte()
    pool_args: list[Any] = [env]
    base_where = f"i.env = ?{search_sql}{source_sql}"
    base_args = pool_args + [env] + search_args + source_args
    sort_norm = (sort or "ingested_at").strip().lower()
    order_norm = (order or "desc").strip().lower()

    with _connect() as conn:
        total_row = conn.execute(
            f"""{pool_cte}
                SELECT COUNT(*) AS n
                  FROM agg a
                  JOIN kol_identity i ON i.id = a.identity_id
                 WHERE {base_where}""",
            base_args,
        ).fetchone()
        counts_row = conn.execute(
            f"""{pool_cte}
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN a.has_discovery = 1 THEN 1 ELSE 0 END) AS discovery,
                       SUM(CASE WHEN a.has_legacy_import = 1 THEN 1 ELSE 0 END) AS legacy
                  FROM agg a
                  JOIN kol_identity i ON i.id = a.identity_id
                 WHERE i.env = ?{search_sql}""",
            pool_args + [env] + search_args,
        ).fetchone()
        rows = conn.execute(
            f"""{pool_cte}
                SELECT a.identity_id AS identity_id,
                       a.first_discovered_at AS first_discovered_at,
                       a.last_seen_at AS last_seen_at,
                       a.has_legacy_import AS has_legacy_import,
                       a.has_discovery AS has_discovery,
                       rc.campaign_id AS latest_campaign_id,
                       i.primary_handle AS primary_handle,
                       i.display_name AS display_name,
                       i.platform AS platform,
                       i.primary_email AS primary_email
                  FROM agg a
                  JOIN kol_identity i ON i.id = a.identity_id
             LEFT JOIN ranked_camp rc
                    ON rc.identity_id = a.identity_id AND rc.rn = 1
                 WHERE {base_where}
              {order_sql}
                 LIMIT ? OFFSET ?""",
            base_args + [limit, offset],
        ).fetchall()
        ids = [int(r["identity_id"]) for r in rows]
        pipeline_by_id = _batch_registry_pipeline_flags(conn, ids, env=env)
        facts_by_id = _batch_registry_facts(
            conn, ids, env=env, fact_keys=KOL_REGISTRY_FACT_KEYS,
        )

    try:
        from . import prior_touch_allowlist as _pta
    except ImportError:
        import prior_touch_allowlist as _pta  # type: ignore[no-redef]

    items: list[dict[str, Any]] = []
    for row in rows:
        iid = int(row["identity_id"])
        handle = row["primary_handle"]
        platform = row["platform"]
        facts = facts_by_id.get(iid, {})
        email = _resolve_registry_email(row["primary_email"], facts)
        touch_count = _pta.get_internal_touch_count(handle=handle, email=email)
        pipeline = pipeline_by_id.get(iid, {})
        items.append({
            "identity_id": iid,
            "handle": handle,
            "display_name": row["display_name"],
            "platform": platform,
            "email": email,
            "ig_url": _pick_instagram_url(
                facts, platform=platform, handle=handle,
            ),
            "internal_touch_count": touch_count,
            "has_initial_outreach_draft": bool(
                pipeline.get("has_initial_outreach_draft"),
            ),
            "has_inbound_reply": bool(pipeline.get("has_inbound_reply")),
            "target_spu": _resolve_registry_target_spu(facts),
            "latest_campaign_id": row["latest_campaign_id"],
            "first_discovered_at": row["first_discovered_at"],
            "has_legacy_import": bool(row["has_legacy_import"]),
            "has_discovery": bool(row["has_discovery"]),
            "followers": _resolve_registry_followers(facts),
            "avg_views": _resolve_registry_avg_views(facts),
            "audience_facts": facts,
        })
    return {
        "total": int(total_row["n"]) if total_row else 0,
        "limit": limit,
        "offset": offset,
        "items": items,
        "env": env,
        "source": (source or "all").lower(),
        "sort": sort_norm if sort_norm in (
            "ingested_at", "first_discovered_at", "created_at",
        ) else "ingested_at",
        "order": order_norm if order_norm in ("asc", "desc") else "desc",
        "counts": {
            "total": int(counts_row["total"]) if counts_row else 0,
            "discovery": int(counts_row["discovery"] or 0) if counts_row else 0,
            "legacy": int(counts_row["legacy"] or 0) if counts_row else 0,
        },
    }


def aggregate_kol_registry_funnel(
    *,
    env: str = "LIVE",
    days: Optional[int] = None,
) -> dict[str, Any]:
    """Funnel metrics for the gate-metrics dashboard.

    * **kol_candidate_adoption_rate** — share of eligible discoveries that
      reached initial outreach draft (approve), excluding prior-collab KOLs
      on the legacy 曾触达 allowlist.
    * **initial_outreach_reply_rate** — share of initial-draft KOLs with an
      inbound reply (``kol_inbound_reply``).
    """
    env_norm = env.upper()
    pool_cte = _registry_pool_cte()
    pool_args: list[Any] = [env_norm]
    time_sql = ""
    time_args: list[Any] = []
    if days is not None and int(days) > 0:
        cutoff = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=int(days))
        ).isoformat(timespec="seconds")
        time_sql = " AND a.first_discovered_at >= ?"
        time_args = [cutoff]

    try:
        from . import prior_touch_allowlist as _pta
    except ImportError:
        import prior_touch_allowlist as _pta  # type: ignore[no-redef]

    with _connect() as conn:
        rows = conn.execute(
            f"""{pool_cte}
                SELECT a.identity_id AS identity_id,
                       i.primary_handle AS primary_handle,
                       i.primary_email AS primary_email
                  FROM agg a
                  JOIN kol_identity i ON i.id = a.identity_id
                 WHERE i.env = ?{time_sql}""",
            pool_args + [env_norm] + time_args,
        ).fetchall()
        ids = [int(r["identity_id"]) for r in rows]
        pipeline_by_id = _batch_registry_pipeline_flags(conn, ids, env=env_norm)
        facts_by_id = _batch_registry_facts(
            conn,
            ids,
            env=env_norm,
            fact_keys=("identity.primary_email_from_legacy",),
        )

    discovered_total = len(rows)
    prior_collab_excluded = 0
    initial_outreach_draft_count = 0
    initial_outreach_reply_count = 0

    for row in rows:
        iid = int(row["identity_id"])
        facts = facts_by_id.get(iid, {})
        email = _resolve_registry_email(row["primary_email"], facts)
        if _pta.is_prior_touch_allowlisted(
            handle=row["primary_handle"], email=email,
        ):
            prior_collab_excluded += 1
            continue
        pipeline = pipeline_by_id.get(iid, {})
        if not pipeline.get("has_initial_outreach_draft"):
            continue
        initial_outreach_draft_count += 1
        if pipeline.get("has_inbound_reply"):
            initial_outreach_reply_count += 1

    eligible_total = discovered_total - prior_collab_excluded
    adoption_rate = (
        initial_outreach_draft_count / eligible_total
        if eligible_total else 0.0
    )
    reply_rate = (
        initial_outreach_reply_count / initial_outreach_draft_count
        if initial_outreach_draft_count else 0.0
    )
    return {
        "env": env_norm,
        "window_days": int(days) if days else None,
        "discovered_total": discovered_total,
        "prior_collab_excluded": prior_collab_excluded,
        "eligible_total": eligible_total,
        "initial_outreach_draft_count": initial_outreach_draft_count,
        "initial_outreach_reply_count": initial_outreach_reply_count,
        "kol_candidate_adoption_rate": adoption_rate,
        "initial_outreach_reply_rate": reply_rate,
    }


def get_reusable_facts(identity_id: int) -> dict[str, Any]:
    """Identity-level facts a re-engagement skill can plausibly reuse."""
    ident = get_identity(identity_id) or {}
    rel = get_relationship(identity_id) or {}
    negotiation_style = rel.get("negotiation_style") or "unknown"
    preferred_mode = rel.get("preferred_mode", "unknown")
    last_outcome = rel.get("last_outcome")
    total_collabs = rel.get("total_collabs", 0)
    hint = _build_personalization_hint(
        last_outcome=last_outcome,
        preferred_mode=preferred_mode,
        negotiation_style=negotiation_style,
        total_collabs=total_collabs,
    )
    return {
        "default_shipping_address": ident.get("default_shipping_address"),
        "default_payment_method": ident.get("default_payment_method"),
        "preferred_skus": rel.get("preferred_skus", []),
        "preferred_mode": preferred_mode,
        "negotiation_style": negotiation_style,
        "personalization_hint": hint,
        "last_outcome": last_outcome,
        "total_collabs": total_collabs,
    }


def _build_personalization_hint(
    *,
    last_outcome: Optional[str],
    preferred_mode: str,
    negotiation_style: str,
    total_collabs: int,
) -> str:
    """Derive 1–2 sentence guidance for repeat-KOL outreach."""
    if total_collabs <= 0 and not last_outcome:
        return ""
    parts: list[str] = []
    if last_outcome == "success":
        parts.append("Prior collaboration completed successfully — lead with warmth, not cold pitch.")
    elif last_outcome:
        parts.append(f"Prior outcome was {last_outcome} — acknowledge history briefly without over-promising.")
    if preferred_mode and preferred_mode not in {"unknown", ""}:
        parts.append(f"They previously preferred {preferred_mode} compensation.")
    if negotiation_style == "hard_anchor":
        parts.append("Expect firm rate anchors; avoid low opening counters.")
    elif negotiation_style == "soft_anchor":
        parts.append("They tend to negotiate flexibly once scope is clear.")
    return " ".join(parts[:2])


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
        "variant_candidates": "variant_candidates_json",
        "followup_intervals": "followup_intervals_json",
    }
    scalar_allowed = {
        "label", "product_display_name", "product_url",
        "product_unit_price", "barter_policy",
        "paid_ceiling", "paid_target_budget", "paid_ratio_override",
        "deliverable_count_per_platform",
        "extra_notes",
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
            nox_updates = pick_nox_fields(fields)
            for k, v in fields.items():
                if k in nox_updates:
                    continue
                if k in json_cols and v is not None:
                    sets.append(f"{json_cols[k]} = ?")
                    vals.append(_j(v))
                elif k in scalar_allowed and v is not None:
                    if k == "contract_required":
                        v = 1 if v else 0
                    sets.append(f"{k} = ?")
                    vals.append(v)
            if nox_updates:
                existing_blob: dict[str, Any] = {}
                if row:
                    cur = conn.execute(
                        "SELECT nox_integration_json FROM campaign_config "
                        "WHERE campaign_id=?",
                        (campaign_id,),
                    ).fetchone()
                    if cur is not None:
                        existing_blob = parse_nox_integration_json(cur[0])
                merged = merge_nox_integration(existing_blob, nox_updates)
                sets.append("nox_integration_json = ?")
                vals.append(encode_nox_integration(merged))
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
    operator-visible shortlist counts.

    ``candidate_count`` matches the product-page / shortlist pool: all
    ``campaign_candidates`` rows except ``rejected`` / ``archived``. This
    is **not** the kanban (``get_lanes``) count — kanban only lists
    ``selected_for_outreach`` (+ ``needs_review`` / ``archived`` lifecycle).

    Left-joins ``campaign_config`` for label/status. Sorted newest-first by
    the candidate row's max ``updated_at``.
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
                       SUM(CASE
                             WHEN c.candidate_status NOT IN ('rejected', 'archived')
                             THEN 1 ELSE 0
                           END)           AS candidate_count,
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
    out["variant_candidates"] = _jl(out.pop("variant_candidates_json", "[]"), [])
    out["followup_intervals"] = _jl(out.pop("followup_intervals_json", "{}"), {})
    out["contract_required"] = bool(out.get("contract_required", 1))
    return flatten_nox_into_config(out)


def batch_global_outreach_touch(
    identity_ids: Iterable[int],
    *,
    env: str = "LIVE",
) -> dict[int, dict[str, Any]]:
    """Cross-campaign last outreach send per identity (for UI tags / cooldown)."""

    def _do() -> dict[int, dict[str, Any]]:
        with _connect() as conn:
            return outreach_touch.batch_global_outreach_touch(
                conn, identity_ids, env=env,
            )

    return _safe("batch_global_outreach_touch", _do) or {}


def list_outreach_cooldown_handles(
    *,
    env: str = "LIVE",
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Handles blocked from discovery by the 14-day outreach cooldown."""

    def _do() -> list[dict[str, Any]]:
        with _connect() as conn:
            return outreach_touch.list_cooldown_handles(conn, env=env, limit=limit)

    return _safe("list_outreach_cooldown_handles", _do) or []


def list_discovery_skip_handles(
    *,
    env: str = "LIVE",
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Handles blocked from discovery by archived ``last_outcome`` values."""

    def _do() -> list[dict[str, Any]]:
        from . import discovery_skip as _ds

        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(handle: Any, reason: str) -> None:
            if len(items) >= limit:
                return
            norm = _ds.normalize_skip_handle(handle)
            if not norm or norm in seen:
                return
            seen.add(norm)
            items.append({"handle": norm, "reason": reason})

        outcomes = sorted(_ds.DISCOVERY_SKIP_OUTCOMES)
        placeholders = ",".join("?" * len(outcomes))
        with _connect() as conn:
            rows = conn.execute(
                f"""SELECT i.primary_handle AS primary_handle,
                           r.last_outcome AS last_outcome
                      FROM kol_identity i
                      JOIN kol_relationship r ON r.identity_id = i.id
                     WHERE i.env = ?
                       AND r.last_outcome IN ({placeholders})
                  ORDER BY i.primary_handle""",
                [env, *outcomes],
            ).fetchall()
        for row in rows:
            _add(row["primary_handle"], str(row["last_outcome"]))
        return items[:limit]

    return _safe("list_discovery_skip_handles", _do) or []


def _assert_outreach_cooldown_clear(*, identity_id: int, env: str) -> None:
    with _connect() as conn:
        touches = outreach_touch.batch_global_outreach_touch(
            conn, [identity_id], env=env,
        )
    touch = touches.get(identity_id)
    if touch and touch.get("within_cooldown"):
        raise outreach_touch.OutreachCooldownActive(
            identity_id=identity_id,
            last_touch_at=str(touch["last_touch_at"]),
            last_touch_campaign_id=touch.get("last_touch_campaign_id"),
        )


def _assert_discovery_not_skipped(*, identity_id: int, env: str) -> None:
    from . import discovery_skip as _ds

    _ds.assert_discovery_not_skipped(identity_id=identity_id, env=env)


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
    enforce_outreach_cooldown: bool = True,
    enforce_discovery_skip: bool = True,
) -> Optional[int]:
    if identity_id is not None:
        if enforce_discovery_skip:
            _assert_discovery_not_skipped(identity_id=int(identity_id), env=env)
        if enforce_outreach_cooldown:
            _assert_outreach_cooldown_clear(identity_id=int(identity_id), env=env)

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            existing = conn.execute(
                "SELECT id FROM campaign_candidates WHERE campaign_id=? AND identity_id=? AND env=?",
                (campaign_id, identity_id, env),
            ).fetchone()
            payload_json = _j(payload or {})
            if existing:
                clear_selection = candidate_status in ("discovered", "shortlisted")
                conn.execute(
                    """UPDATE campaign_candidates SET
                          source = ?, discovery_score = COALESCE(?, discovery_score),
                          relationship_status = ?, candidate_status = ?,
                          review_reason = COALESCE(?, review_reason),
                          payload_json = ?, updated_at = ?,
                          selected_by = CASE WHEN ? THEN NULL ELSE selected_by END,
                          selected_at = CASE WHEN ? THEN NULL ELSE selected_at END
                       WHERE id = ?""",
                    (source, discovery_score, relationship_status, candidate_status,
                     review_reason, payload_json, now,
                     clear_selection, clear_selection, existing["id"]),
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


def _guess_profile_url(platform: Any, handle: Any) -> str | None:
    """Best-effort public profile URL when CAL has no stored profile link."""
    if not isinstance(handle, str):
        return None
    h = handle.strip().lstrip("@")
    if not h:
        return None
    p = (str(platform).strip().lower() if platform else "") or "instagram"
    if p == "tiktok":
        return f"https://www.tiktok.com/@{h}"
    if p == "youtube":
        return f"https://www.youtube.com/@{h}"
    if p in {"twitter", "x"}:
        return f"https://x.com/{h}"
    if p == "facebook":
        return f"https://www.facebook.com/{h}"
    if p == "threads":
        return f"https://www.threads.net/@{h}"
    return f"https://www.instagram.com/{h}/"


def list_candidate_handles(campaign_id: str, *, env: str = "LIVE") -> list[dict[str, Any]]:
    """Return campaign candidates joined to their canonical identity handles."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT c.id                  AS candidate_id,
                      c.identity_id         AS identity_id,
                      i.primary_handle      AS handle,
                      i.display_name        AS display_name,
                      i.platform            AS platform,
                      c.source              AS source,
                      c.discovery_score     AS discovery_score,
                      c.relationship_status AS relationship_status,
                      c.candidate_status    AS candidate_status,
                      c.review_reason       AS review_reason,
                      c.selected_at         AS selected_at,
                      c.payload_json        AS payload_json,
                      c.created_at          AS created_at,
                      c.updated_at          AS updated_at,
                      r.total_collabs       AS total_collabs,
                      r.last_outcome        AS last_outcome
                 FROM campaign_candidates c
            LEFT JOIN kol_identity i ON i.id = c.identity_id
            LEFT JOIN kol_relationship r ON r.identity_id = c.identity_id
                WHERE c.campaign_id=? AND c.env=?
             ORDER BY c.id""",
            (campaign_id, env),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        payload = _jl(item.pop("payload_json", "{}"), {})
        handle = item.get("handle")
        if isinstance(handle, str):
            handle = handle.strip().lstrip("@")
            item["handle"] = handle or None
        platform = item.get("platform")
        handle = item.get("handle")
        if handle:
            item["profile_url"] = _guess_profile_url(platform, handle)
        else:
            item["profile_url"] = None
        item["payload"] = payload
        items.append(item)
    return items


def get_candidate_for(
    *, identity_id: int, campaign_id: str, env: str = "LIVE"
) -> Optional[dict[str, Any]]:
    """Return the single ``campaign_candidates`` row for an (identity, campaign,
    env) triple, with ``payload_json`` decoded into ``payload``. Returns None
    when the identity is not a candidate of this campaign — drafters need this
    so they can tell "no per-campaign evidence" apart from "evidence is empty".
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM campaign_candidates
                WHERE identity_id=? AND campaign_id=? AND env=?""",
            (identity_id, campaign_id, env),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = _jl(d.pop("payload_json", "{}"), {})
    return d


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
            clear_selection = candidate_status in ("discovered", "shortlisted")
            cur = conn.execute(
                f"""UPDATE campaign_candidates
                       SET candidate_status=?,
                           review_reason=COALESCE(?, review_reason),
                           updated_at=?,
                           selected_by = CASE WHEN ? THEN NULL ELSE selected_by END,
                           selected_at = CASE WHEN ? THEN NULL ELSE selected_at END
                     WHERE campaign_id=? AND env=? AND identity_id IN ({qmarks})""",
                [candidate_status, review_reason, now,
                 clear_selection, clear_selection, campaign_id, env, *ids],
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
    "offer.kol_quoted_amount":               ("compensation.kol_quoted",       "compensation_negotiation", "commerce"),
    "offer.barter_attempted":                ("compensation.barter_attempted", "compensation_negotiation", "commerce"),
    "offer.rate_requested":                  ("compensation.rate_requested",   "compensation_negotiation", "commerce"),
    "offer.proposed_amount":                 ("compensation.proposed_amount",  "compensation_negotiation", "commerce"),
    "offer.compensation_mode":               ("compensation.mode_set",         "compensation_negotiation", "commerce"),
    "offer.agreed_terms":                    ("compensation.agreed",           "compensation_negotiation", "commerce"),
    "offer.contract_sent":                   ("contract.sent",                 "contract_signing",         "commerce"),
    "offer.contract_signed":                 ("contract.signed",               "contract_signing",         "commerce"),
    "offer.contract_declined_reason":        ("contract.declined",             "contract_signing",         "commerce"),
    "fulfillment.address_collected":         ("logistics.address_collected",   "logistics",               "fulfillment"),
    "fulfillment.shipping_method":           ("logistics.shipping_method_set", "logistics",               "fulfillment"),
    "fulfillment.tracking_filled":           ("logistics.tracking_filled",     "logistics",               "fulfillment"),
    "fulfillment.delivered_confirmed":       ("logistics.delivered",           "logistics",               "fulfillment"),
    "payout.method_collected":               ("payout.method_collected",       "payout_setup",            "fulfillment"),
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
    if not reply_draft.has_thread_anchor(value):
        raise FactNamespaceError(
            "approval.reply_draft must carry a thread anchor: "
            "draft.thread_id, source_message_id, top-level thread_id, or in_reply_to"
        )


def _validate_reply_draft_write_source(source: str) -> None:
    """Block direct skill writes that bypass ``persist-reply-draft``."""
    if source.startswith("skill:kol-"):
        raise FactNamespaceError(
            "approval.reply_draft must be written via persist-reply-draft "
            "(source draft:<message_id>), not skill write-facts"
        )


def _validate_chase_pending_action_guard(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    facts: Mapping[str, Any],
    source: str,
) -> None:
    """Reject pending_action when chase policy requires draft regeneration."""
    if not campaign_id:
        return
    if not facts.get("approval.pending_action_reply_needed"):
        return
    if not _truthy(facts.get("approval.pending_action_reply_needed")):
        return
    inbound_message_id: str | None = None
    if source.startswith("email:"):
        inbound_message_id = source[len("email:"):].strip() or None
    if not inbound_message_id:
        return
    chase = reply_chase_hint(
        identity_id=identity_id,
        campaign_id=campaign_id,
        message_id=inbound_message_id,
        thread_id=None,
        env=env,
    )
    if chase.get("recommended_action") == "regenerate":
        raise FactNamespaceError(
            "approval.pending_action_reply_needed is blocked for follow-up inbound "
            f"{inbound_message_id}: use persist-reply-draft to supersede the stale "
            "pending draft (see chase_context.recommended_action=regenerate)"
        )


def _validate_approval_write_guards(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    facts: Mapping[str, Any],
    source: str,
) -> None:
    if "approval.reply_draft" in facts:
        _validate_reply_draft_write_source(source)
    _validate_chase_pending_action_guard(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        facts=facts,
        source=source,
    )


_DISCOVERY_ALLOWED_SOURCES: Final[set[str]] = {
    "google_search_result",
    "linktree",
    "ig_bio",
    "facebook_about",
    "fb_creator_profile",
    "personal_site",
    "media_kit",
    "agency_page",
    "ig_profile_and_reels",
    "ig_reel_pick",
    "llm_summary",
    "noxinfluencer_api",
}

_DISCOVERY_BASE_KEYS_REQUIRING_TRIPLE: Final[set[str]] = {
    "identity.content_pillars",
    "identity.signature_hooks",
    "identity.voice_descriptors",
    "identity.hero_post_url",
    "identity.hero_post_note",
    "identity.recommendation_reason",
    "identity.instagram_profile_url",
    "identity.tiktok_profile_url",
    "identity.youtube_profile_url",
    "identity.facebook_profile_url",
    "identity.twitter_profile_url",
    "identity.threads_profile_url",
    "identity.linktree_url",
    "identity.personal_site_url",
}


def _require_non_empty_string(*, key: str, value: Any, max_len: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactNamespaceError(f"{key} must be a non-empty string")
    s = value.strip()
    if len(s) > max_len:
        raise FactNamespaceError(f"{key} is too long (>{max_len})")
    return s


def _require_url(*, key: str, value: Any, allowed_hosts: Optional[tuple[str, ...]] = None) -> str:
    s = _require_non_empty_string(key=key, value=value, max_len=1000)
    parsed = urlparse(s)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FactNamespaceError(
            f"{key} must be an absolute http(s) URL; got {s!r}"
        )
    host = parsed.netloc.lower()
    if allowed_hosts is not None:
        if not any(host == h or host.endswith(f".{h}") for h in allowed_hosts):
            raise FactNamespaceError(
                f"{key} must be on one of: {', '.join(allowed_hosts)}; got host {host!r}"
            )
    return s


def _require_iso8601(*, key: str, value: Any) -> None:
    s = _require_non_empty_string(key=key, value=value, max_len=64)
    normalized = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        _dt.datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - exact parser msg not stable
        raise FactNamespaceError(f"{key} must be ISO-8601 timestamp") from exc


def _validate_positive_int(*, key: str, value: Any) -> None:
    if not isinstance(value, int) or value <= 0:
        raise FactNamespaceError(f"{key} must be a positive int")


def _validate_non_negative_int(*, key: str, value: Any) -> None:
    if not isinstance(value, int) or value < 0:
        raise FactNamespaceError(f"{key} must be a non-negative int")


def _validate_object_list(
    *,
    key: str,
    value: Any,
    min_items: int,
    max_items: int,
) -> None:
    if not isinstance(value, list):
        raise FactNamespaceError(f"{key} must be a list")
    n = len(value)
    if n < min_items or n > max_items:
        raise FactNamespaceError(f"{key} must contain {min_items}-{max_items} items")
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise FactNamespaceError(f"{key}[{idx}] must be an object")


def _validate_string_list(
    *,
    key: str,
    value: Any,
    min_items: int,
    max_items: int,
    item_max_len: int,
) -> None:
    if not isinstance(value, list):
        raise FactNamespaceError(f"{key} must be a list")
    n = len(value)
    if n < min_items or n > max_items:
        raise FactNamespaceError(
            f"{key} must contain {min_items}-{max_items} items"
        )
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise FactNamespaceError(f"{key}[{idx}] must be a non-empty string")
        if len(item.strip()) > item_max_len:
            raise FactNamespaceError(f"{key}[{idx}] is too long (>{item_max_len})")


def _validate_instagram_media_url(value: Any) -> None:
    s = _require_url(
        key="identity.hero_post_url",
        value=value,
        allowed_hosts=("instagram.com",),
    )
    parsed = urlparse(s)
    host = parsed.netloc.lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        raise FactNamespaceError(
            "identity.hero_post_url must use instagram.com or www.instagram.com"
        )
    # Only accept direct content URLs; disallow share/redirect helpers and
    # query/fragment based tracking links that often bounce elsewhere.
    if parsed.query or parsed.fragment:
        raise FactNamespaceError(
            "identity.hero_post_url must be a direct URL without query/fragment"
        )
    # Canonical-only guard: reject handle-prefixed variants like
    # /<handle>/reel/<id> because they can visually imply a wrong owner and
    # may bounce to another creator's canonical post URL.
    if not re.fullmatch(r"/(?:reel|p)/[A-Za-z0-9_-]+/?", parsed.path):
        raise FactNamespaceError(
            "identity.hero_post_url must be canonical /reel/<id> or /p/<id>"
        )


def _validate_discovery_source(value: Any) -> None:
    s = _require_non_empty_string(key="identity.*_source", value=value, max_len=64)
    if s not in _DISCOVERY_ALLOWED_SOURCES:
        allowed = ", ".join(sorted(_DISCOVERY_ALLOWED_SOURCES))
        raise FactNamespaceError(
            "identity.*_source contains unsupported source value "
            f"{s!r}; allowed values: {allowed}"
        )


def _validate_discovery_provenance_bundle(*, namespace: str, facts: Mapping[str, Any]) -> None:
    if namespace != "identity":
        return
    for base in _DISCOVERY_BASE_KEYS_REQUIRING_TRIPLE:
        if base not in facts:
            continue
        missing = [
            f"{base}_source",
            f"{base}_discovered_at",
            f"{base}_discovered_url",
        ]
        missing = [k for k in missing if k not in facts]
        if missing:
            raise FactNamespaceError(
                f"{base} requires provenance keys in same write: {', '.join(missing)}"
            )


def _extract_instagram_profile_handle(url: str) -> Optional[str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        return None
    if parsed.query or parsed.fragment:
        return None
    m = re.fullmatch(r"/([A-Za-z0-9._]+)/?", parsed.path or "")
    if not m:
        return None
    return m.group(1).lower()


def _read_identity_primary_handle(*, identity_id: int, env: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT primary_handle
               FROM kol_identity
               WHERE id=? AND env=?
               LIMIT 1""",
            (identity_id, env),
        ).fetchone()
    if not row:
        return None
    raw = row["primary_handle"]
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip().lower()


def _validate_discovery_identity_match(
    *,
    identity_id: int,
    namespace: str,
    facts: Mapping[str, Any],
    env: str,
) -> None:
    if namespace != "identity" or "identity.hero_post_url" not in facts:
        return
    discovered_url = facts.get("identity.hero_post_url_discovered_url")
    if not isinstance(discovered_url, str):
        # Detailed type/shape error is covered by per-key validators.
        return
    owner_handle = _extract_instagram_profile_handle(discovered_url)
    if owner_handle is None:
        raise FactNamespaceError(
            "identity.hero_post_url_discovered_url must be the creator's instagram profile URL for owner verification"
        )
    expected_handle = _read_identity_primary_handle(identity_id=identity_id, env=env)
    if expected_handle is None:
        raise FactNamespaceError(
            "identity_id not found for hero_post_url owner verification"
        )
    if owner_handle != expected_handle:
        raise FactNamespaceError(
            "hero_post_url owner mismatch: discovered profile handle does not match identity.primary_handle"
        )


_FACT_SHAPE_VALIDATORS: Final[dict[str, Callable[[Any], None]]] = {
    "approval.reply_draft": _validate_approval_reply_draft,
    "identity.content_pillars": lambda v: _validate_string_list(
        key="identity.content_pillars", value=v, min_items=2, max_items=4, item_max_len=80,
    ),
    "identity.signature_hooks": lambda v: _validate_string_list(
        key="identity.signature_hooks", value=v, min_items=2, max_items=3, item_max_len=80,
    ),
    "identity.voice_descriptors": lambda v: _validate_string_list(
        key="identity.voice_descriptors", value=v, min_items=2, max_items=3, item_max_len=40,
    ),
    "identity.hero_post_url": _validate_instagram_media_url,
    "identity.hero_post_note": lambda v: _require_non_empty_string(
        key="identity.hero_post_note", value=v, max_len=300,
    ),
    "identity.recommendation_reason": lambda v: _require_non_empty_string(
        key="identity.recommendation_reason", value=v, max_len=500,
    ),
    "identity.instagram_profile_url": lambda v: _require_url(
        key="identity.instagram_profile_url", value=v, allowed_hosts=("instagram.com",),
    ),
    "identity.tiktok_profile_url": lambda v: _require_url(
        key="identity.tiktok_profile_url", value=v, allowed_hosts=("tiktok.com",),
    ),
    "identity.youtube_profile_url": lambda v: _require_url(
        key="identity.youtube_profile_url", value=v, allowed_hosts=("youtube.com", "youtu.be"),
    ),
    "identity.facebook_profile_url": lambda v: _require_url(
        key="identity.facebook_profile_url", value=v, allowed_hosts=("facebook.com", "fb.com"),
    ),
    "identity.twitter_profile_url": lambda v: _require_url(
        key="identity.twitter_profile_url", value=v, allowed_hosts=("twitter.com", "x.com"),
    ),
    "identity.threads_profile_url": lambda v: _require_url(
        key="identity.threads_profile_url", value=v, allowed_hosts=("threads.net", "threads.com"),
    ),
    "identity.linktree_url": lambda v: _require_url(
        key="identity.linktree_url",
        value=v,
        allowed_hosts=("linktr.ee", "beacons.ai", "bio.link", "lnk.bio", "solo.to", "linkin.bio"),
    ),
    "identity.personal_site_url": lambda v: _require_url(
        key="identity.personal_site_url", value=v,
    ),
    "identity.profile_og_image_url": lambda v: _require_url(
        key="identity.profile_og_image_url", value=v,
    ),
    "identity.profile_og_source_url": lambda v: _require_url(
        key="identity.profile_og_source_url", value=v,
    ),
    "identity.profile_og_title": lambda v: _require_non_empty_string(
        key="identity.profile_og_title", value=v, max_len=300,
    ),
    "identity.profile_og_description": lambda v: _require_non_empty_string(
        key="identity.profile_og_description", value=v, max_len=600,
    ),
    "identity.profile_og_fetched_at": lambda v: _require_iso8601(
        key="identity.profile_og_fetched_at", value=v,
    ),
    "identity.hero_post_url_source": _validate_discovery_source,
    "identity.hero_post_note_source": _validate_discovery_source,
    "identity.recommendation_reason_source": _validate_discovery_source,
    "identity.content_pillars_source": _validate_discovery_source,
    "identity.signature_hooks_source": _validate_discovery_source,
    "identity.voice_descriptors_source": _validate_discovery_source,
    "identity.instagram_profile_url_source": _validate_discovery_source,
    "identity.tiktok_profile_url_source": _validate_discovery_source,
    "identity.youtube_profile_url_source": _validate_discovery_source,
    "identity.facebook_profile_url_source": _validate_discovery_source,
    "identity.twitter_profile_url_source": _validate_discovery_source,
    "identity.threads_profile_url_source": _validate_discovery_source,
    "identity.linktree_url_source": _validate_discovery_source,
    "identity.personal_site_url_source": _validate_discovery_source,
    "identity.hero_post_url_discovered_at": lambda v: _require_iso8601(
        key="identity.hero_post_url_discovered_at", value=v,
    ),
    "identity.hero_post_note_discovered_at": lambda v: _require_iso8601(
        key="identity.hero_post_note_discovered_at", value=v,
    ),
    "identity.recommendation_reason_discovered_at": lambda v: _require_iso8601(
        key="identity.recommendation_reason_discovered_at", value=v,
    ),
    "identity.content_pillars_discovered_at": lambda v: _require_iso8601(
        key="identity.content_pillars_discovered_at", value=v,
    ),
    "identity.signature_hooks_discovered_at": lambda v: _require_iso8601(
        key="identity.signature_hooks_discovered_at", value=v,
    ),
    "identity.voice_descriptors_discovered_at": lambda v: _require_iso8601(
        key="identity.voice_descriptors_discovered_at", value=v,
    ),
    "identity.instagram_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.instagram_profile_url_discovered_at", value=v,
    ),
    "identity.tiktok_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.tiktok_profile_url_discovered_at", value=v,
    ),
    "identity.youtube_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.youtube_profile_url_discovered_at", value=v,
    ),
    "identity.facebook_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.facebook_profile_url_discovered_at", value=v,
    ),
    "identity.twitter_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.twitter_profile_url_discovered_at", value=v,
    ),
    "identity.threads_profile_url_discovered_at": lambda v: _require_iso8601(
        key="identity.threads_profile_url_discovered_at", value=v,
    ),
    "identity.linktree_url_discovered_at": lambda v: _require_iso8601(
        key="identity.linktree_url_discovered_at", value=v,
    ),
    "identity.personal_site_url_discovered_at": lambda v: _require_iso8601(
        key="identity.personal_site_url_discovered_at", value=v,
    ),
    "identity.hero_post_url_discovered_url": lambda v: _require_url(
        key="identity.hero_post_url_discovered_url", value=v,
    ),
    "identity.hero_post_note_discovered_url": lambda v: _require_url(
        key="identity.hero_post_note_discovered_url", value=v,
    ),
    "identity.recommendation_reason_discovered_url": lambda v: _require_url(
        key="identity.recommendation_reason_discovered_url", value=v,
    ),
    "identity.content_pillars_discovered_url": lambda v: _require_url(
        key="identity.content_pillars_discovered_url", value=v,
    ),
    "identity.signature_hooks_discovered_url": lambda v: _require_url(
        key="identity.signature_hooks_discovered_url", value=v,
    ),
    "identity.voice_descriptors_discovered_url": lambda v: _require_url(
        key="identity.voice_descriptors_discovered_url", value=v,
    ),
    "identity.instagram_profile_url_discovered_url": lambda v: _require_url(
        key="identity.instagram_profile_url_discovered_url", value=v,
    ),
    "identity.tiktok_profile_url_discovered_url": lambda v: _require_url(
        key="identity.tiktok_profile_url_discovered_url", value=v,
    ),
    "identity.youtube_profile_url_discovered_url": lambda v: _require_url(
        key="identity.youtube_profile_url_discovered_url", value=v,
    ),
    "identity.facebook_profile_url_discovered_url": lambda v: _require_url(
        key="identity.facebook_profile_url_discovered_url", value=v,
    ),
    "identity.twitter_profile_url_discovered_url": lambda v: _require_url(
        key="identity.twitter_profile_url_discovered_url", value=v,
    ),
    "identity.threads_profile_url_discovered_url": lambda v: _require_url(
        key="identity.threads_profile_url_discovered_url", value=v,
    ),
    "identity.linktree_url_discovered_url": lambda v: _require_url(
        key="identity.linktree_url_discovered_url", value=v,
    ),
    "identity.personal_site_url_discovered_url": lambda v: _require_url(
        key="identity.personal_site_url_discovered_url", value=v,
    ),
    "identity.veedcrawl_cache_month": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_cache_month", value=v, max_len=7,
    ),
    "identity.veedcrawl_cache_key": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_cache_key", value=v, max_len=256,
    ),
    "identity.veedcrawl_fetched_at": lambda v: _require_iso8601(
        key="identity.veedcrawl_fetched_at", value=v,
    ),
    "identity.veedcrawl_storage_ref": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_storage_ref", value=v, max_len=500,
    ),
    "identity.veedcrawl_blob_ref": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_blob_ref", value=v, max_len=500,
    ),
    "identity.veedcrawl_profile_handle": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_profile_handle", value=v, max_len=64,
    ),
    "identity.veedcrawl_profile_followers": lambda v: _validate_positive_int(
        key="identity.veedcrawl_profile_followers", value=v,
    ),
    "identity.veedcrawl_last_reel_views": lambda v: _validate_non_negative_int(
        key="identity.veedcrawl_last_reel_views", value=v,
    ),
    "identity.veedcrawl_last_reel_likes": lambda v: _validate_non_negative_int(
        key="identity.veedcrawl_last_reel_likes", value=v,
    ),
    "identity.veedcrawl_last_reel_url": lambda v: _require_url(
        key="identity.veedcrawl_last_reel_url", value=v, allowed_hosts=("instagram.com",),
    ),
    "identity.veedcrawl_extract_summary": lambda v: _require_non_empty_string(
        key="identity.veedcrawl_extract_summary", value=v, max_len=4000,
    ),
    "identity.veedcrawl_recent_reels_stats": lambda v: _validate_object_list(
        key="identity.veedcrawl_recent_reels_stats", value=v, min_items=1, max_items=24,
    ),
    "identity.veedcrawl_search_authors": lambda v: _validate_string_list(
        key="identity.veedcrawl_search_authors", value=v, min_items=1, max_items=20, item_max_len=64,
    ),
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
    _validate_discovery_provenance_bundle(namespace=namespace, facts=facts)
    _validate_discovery_identity_match(
        identity_id=identity_id, namespace=namespace, facts=facts, env=env,
    )
    _validate_approval_write_guards(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        facts=facts,
        source=source,
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


def write_identity_facts_batch(
    *,
    entries: list[tuple[int, Mapping[str, Any]]],
    campaign_id: str,
    namespace: str,
    source: str,
    env: str = "LIVE",
) -> int:
    """Write the same namespace facts for many identities with one recompute each."""
    if namespace not in FACT_NAMESPACES:
        raise FactNamespaceError(f"unknown namespace: {namespace!r}")
    prefix = f"{namespace}."
    for identity_id, facts in entries:
        for k, v in facts.items():
            if not k.startswith(prefix):
                raise FactNamespaceError(
                    f"fact_key {k!r} must start with {prefix!r}"
                )
            validator = _FACT_SHAPE_VALIDATORS.get(k)
            if validator is not None:
                validator(v)
        _validate_discovery_provenance_bundle(namespace=namespace, facts=facts)
        _validate_discovery_identity_match(
            identity_id=identity_id, namespace=namespace, facts=facts, env=env,
        )
        _validate_approval_write_guards(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            facts=facts,
            source=source,
        )

    def _do() -> int:
        with _connect() as conn:
            now = _now()
            n = 0
            touched: set[int] = set()
            for identity_id, facts in entries:
                for k, v in facts.items():
                    conn.execute(
                        """INSERT INTO kol_facts
                           (identity_id, campaign_id, fact_namespace, fact_key,
                            fact_value, source, source_event_id, captured_at, env)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (identity_id, campaign_id, namespace, k,
                         _j(v) if not isinstance(v, str) else v,
                         source, None, now, env),
                    )
                    n += 1
                touched.add(int(identity_id))
            for identity_id in touched:
                _recompute_goals_inner(
                    conn,
                    identity_id=identity_id,
                    campaign_id=campaign_id,
                    env=env,
                )
            return n

    return _safe("write_identity_facts_batch", _do)


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
    call before any insert).     All namespaces are inserted in one transaction with a single goal
    recompute at the end.

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
        _validate_discovery_provenance_bundle(namespace=ns, facts=facts)
        _validate_discovery_identity_match(
            identity_id=identity_id, namespace=ns, facts=facts, env=env,
        )
        _validate_approval_write_guards(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            facts=facts,
            source=source,
        )

    def _do() -> dict[str, int]:
        with _connect() as conn:
            now = _now()
            written: dict[str, int] = {}
            for ns, facts in namespaces.items():
                if not facts:
                    continue
                n = 0
                for k, v in facts.items():
                    conn.execute(
                        """INSERT INTO kol_facts
                           (identity_id, campaign_id, fact_namespace, fact_key,
                            fact_value, source, source_event_id, captured_at, env)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (identity_id, campaign_id, ns, k,
                         _j(v) if not isinstance(v, str) else v,
                         source, source_event_id, now, env),
                    )
                    n += 1
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
                written[ns] = n
            if campaign_id:
                _recompute_goals_inner(
                    conn,
                    identity_id=identity_id,
                    campaign_id=campaign_id,
                    env=env,
                )
            return written

    result = _safe("write_facts_multi", _do)
    return result if result is not None else {}


def latest_facts_for(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str = "LIVE",
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Return the latest value per fact_key for an (identity, campaign)
    pair, with identity-level facts (campaign_id IS NULL) merged underneath
    so thread-level overrides win.

    When ``conn`` is supplied, queries reuse that connection (avoids extra
    WAL opens during ``recompute_goals`` hot paths).
    """

    def _decode(v: Any) -> Any:
        if not isinstance(v, str):
            return v
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return v

    def _read(c: sqlite3.Connection) -> dict[str, Any]:
        ident_rows = c.execute(
            """SELECT fact_key, fact_value FROM kol_facts_latest
                WHERE identity_id=? AND campaign_id IS NULL AND env=?""",
            (identity_id, env),
        ).fetchall()
        camp_rows = []
        if campaign_id:
            camp_rows = c.execute(
                """SELECT fact_key, fact_value FROM kol_facts_latest
                    WHERE identity_id=? AND campaign_id=? AND env=?""",
                (identity_id, campaign_id, env),
            ).fetchall()
        out: dict[str, Any] = {
            r["fact_key"]: _decode(r["fact_value"]) for r in ident_rows
        }
        for r in camp_rows:
            out[r["fact_key"]] = _decode(r["fact_value"])
        return out

    if conn is not None:
        return _read(conn)
    with _connect() as owned:
        return _read(owned)


def recompute_goals(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> int:
    with _connect() as conn:
        return _recompute_goals_inner(conn, identity_id=identity_id,
                                      campaign_id=campaign_id, env=env) or 0


def _recompute_goals_inner(
    conn: sqlite3.Connection, *, identity_id: int, campaign_id: str, env: str
) -> int:
    state = latest_facts_for(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        conn=conn,
    )
    cfg_row = conn.execute(
        "SELECT * FROM campaign_config WHERE campaign_id=?", (campaign_id,)
    ).fetchone()
    cfg: dict[str, Any] = dict(cfg_row) if cfg_row else {}
    if cfg:
        cfg["contract_required"] = bool(cfg.get("contract_required", 1))
        cfg["sku_whitelist"] = _jl(cfg.get("sku_whitelist_json"), [])
        cfg["variant_candidates"] = _jl(cfg.get("variant_candidates_json"), [])
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


# Fact keys the Web kanban reads per card (subset of latest_facts_for).
KANBAN_FACT_KEYS: Final[tuple[str, ...]] = (
    "offer.outreach_sent_at",
    "offer.interest_signal",
    "offer.outreach_draft_created",
    "offer.gmail_draft_id",
    "offer.gmail_thread_id",
    "offer.outreach_sent",
    "approval.reply_draft",
)

SHORTLIST_NOX_FACT_KEYS: Final[tuple[str, ...]] = (
    "identity.nox_diligence_verdict",
    "identity.nox_cache_month",
    "identity.nox_creator_id",
)


def _decode_fact_value(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return v


def _build_goal_state_list(by_name: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in GOAL_NAMES:
        r = by_name.get(name)
        if not r:
            out.append({
                "goal": name,
                "status": "inactive",
                "lane": GOALS[name].lane,
                "missing_facts": list(GOALS[name].required_facts),
            })
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


def _goal_states_to_lanes_view(state_list: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {
        "commerce": [],
        "fulfillment": [],
        "publish": [],
        "meta": [],
    }
    for s in state_list:
        out.setdefault(s["lane"], []).append(s)
    return out


def get_goal_state(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT goal, status, lane, missing_facts_json, blocking_escalation_id,
                      updated_at
                 FROM kol_goal_state
                WHERE identity_id=? AND campaign_id=? AND env=?""",
            (identity_id, campaign_id, env),
        ).fetchall()
    return _build_goal_state_list({r["goal"]: r for r in rows})


def get_lanes_view(*, identity_id: int, campaign_id: str, env: str = "LIVE") -> dict[str, list[dict[str, Any]]]:
    return _goal_states_to_lanes_view(
        get_goal_state(identity_id=identity_id, campaign_id=campaign_id, env=env),
    )


def batch_relationship_summaries(
    identity_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Lightweight relationship rows for list views (no collab_history)."""
    ids = [int(i) for i in identity_ids if i is not None]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))

    def _do() -> dict[int, dict[str, Any]]:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM kol_relationship WHERE identity_id IN ({placeholders})",
                ids,
            ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            d = dict(r)
            d["preferred_skus"] = _jl(d.pop("preferred_skus_json", "[]"), [])
            out[int(d["identity_id"])] = d
        return out

    return _safe("batch_relationship_summaries", _do) or {}


def batch_latest_facts_subset(
    *,
    campaign_id: str,
    identity_ids: Iterable[int],
    env: str = "LIVE",
    fact_keys: Iterable[str],
) -> dict[int, dict[str, Any]]:
    """Latest values for ``fact_keys`` across many identities (one campaign)."""
    ids = [int(i) for i in identity_ids if i is not None]
    if not ids:
        return {}
    keys = [str(k) for k in fact_keys if k]
    if not keys:
        return {i: {} for i in ids}
    id_ph = ",".join("?" * len(ids))
    key_ph = ",".join("?" * len(keys))

    def _do() -> dict[int, dict[str, Any]]:
        with _connect() as conn:
            ident_rows = conn.execute(
                f"""SELECT identity_id, fact_key, fact_value FROM kol_facts_latest
                     WHERE identity_id IN ({id_ph}) AND campaign_id IS NULL AND env=?
                       AND fact_key IN ({key_ph})""",
                (*ids, env, *keys),
            ).fetchall()
            camp_rows = conn.execute(
                f"""SELECT identity_id, fact_key, fact_value FROM kol_facts_latest
                     WHERE identity_id IN ({id_ph}) AND campaign_id=? AND env=?
                       AND fact_key IN ({key_ph})""",
                (*ids, campaign_id, env, *keys),
            ).fetchall()
        out: dict[int, dict[str, Any]] = {i: {} for i in ids}
        for r in ident_rows:
            out[int(r["identity_id"])][r["fact_key"]] = _decode_fact_value(r["fact_value"])
        for r in camp_rows:
            out[int(r["identity_id"])][r["fact_key"]] = _decode_fact_value(r["fact_value"])
        return out

    return _safe("batch_latest_facts_subset", _do) or {}


def batch_kanban_facts(
    *,
    campaign_id: str,
    identity_ids: Iterable[int],
    env: str = "LIVE",
) -> dict[int, dict[str, Any]]:
    """Latest fact subset for many identities in one campaign (kanban cards)."""
    return batch_latest_facts_subset(
        campaign_id=campaign_id,
        identity_ids=identity_ids,
        env=env,
        fact_keys=KANBAN_FACT_KEYS,
    )


def batch_identity_briefs(identity_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Minimal identity rows for list UIs (handle / display_name / platform)."""
    ids = [int(i) for i in identity_ids if i is not None]
    if not ids:
        return {}

    def _do() -> dict[int, dict[str, Any]]:
        placeholders = ",".join("?" * len(ids))
        with _connect() as conn:
            rows = conn.execute(
                f"""SELECT id, primary_handle, display_name, platform
                      FROM kol_identity WHERE id IN ({placeholders})""",
                ids,
            ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            handle = r["primary_handle"]
            if isinstance(handle, str):
                handle = handle.strip().lstrip("@") or None
            out[int(r["id"])] = {
                "id": int(r["id"]),
                "primary_handle": handle,
                "display_name": r["display_name"],
                "platform": r["platform"],
            }
        return out

    return _safe("batch_identity_briefs", _do) or {}


def batch_lanes_views_for_campaign(
    campaign_id: str,
    *,
    env: str = "LIVE",
    identity_ids: Iterable[int],
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """All per-identity lane snapshots for one campaign in a single query."""
    ids = [int(i) for i in identity_ids if i is not None]
    if not ids:
        return {}

    def _do() -> dict[int, dict[str, list[dict[str, Any]]]]:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT identity_id, goal, status, lane, missing_facts_json,
                          blocking_escalation_id, updated_at
                     FROM kol_goal_state
                    WHERE campaign_id=? AND env=?""",
                (campaign_id, env),
            ).fetchall()
        grouped: dict[int, dict[str, Mapping[str, Any]]] = {}
        for r in rows:
            iid = int(r["identity_id"])
            if iid not in ids:
                continue
            grouped.setdefault(iid, {})[r["goal"]] = r
        return {
            iid: _goal_states_to_lanes_view(_build_goal_state_list(grouped.get(iid, {})))
            for iid in ids
        }

    return _safe("batch_lanes_views_for_campaign", _do) or {}


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


def get_reply_draft_row(
    *,
    identity_id: int,
    campaign_id: str,
    env: str = "LIVE",
) -> Optional[dict[str, Any]]:
    """Latest ``approval.reply_draft`` row with capture timestamp."""

    def _do() -> Optional[dict[str, Any]]:
        with _connect() as conn:
            row = conn.execute(
                """SELECT fact_value, captured_at FROM kol_facts
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND fact_key='approval.reply_draft'
                    ORDER BY id DESC LIMIT 1""",
                (identity_id, campaign_id, env),
            ).fetchone()
        if not row:
            return None
        val = _jl(row["fact_value"], {})
        return {
            "value": val if isinstance(val, dict) else {},
            "captured_at": row["captured_at"],
        }

    return _safe("get_reply_draft_row", _do)


def collect_campaign_thread_ids(
    *,
    identity_id: int,
    campaign_id: str,
    env: str = "LIVE",
    limit: int = 200,
) -> set[str]:
    """Collect Gmail thread ids referenced in recent conversation events."""
    threads: set[str] = set()
    for ev in list_events(
        env=env,
        identity_id=identity_id,
        campaign_id=campaign_id,
        limit=limit,
    ):
        payload = ev.get("payload") if isinstance(ev, dict) else None
        if not isinstance(payload, dict):
            continue
        tid = payload.get("thread_id")
        if isinstance(tid, str) and tid.strip():
            threads.add(tid.strip())
        gmail_draft = payload.get("gmail_draft")
        if isinstance(gmail_draft, dict):
            gd_tid = gmail_draft.get("thread_id")
            if isinstance(gd_tid, str) and gd_tid.strip():
                threads.add(gd_tid.strip())
    return threads


def has_awaiting_escalation(
    *,
    identity_id: int,
    campaign_id: str,
    env: str = "LIVE",
    goal: str | None = None,
) -> bool:
    """True when an operator-visible escalation is still awaiting_answer."""
    rows = list_escalations(
        state="awaiting_answer",
        env=env,
        identity_id=identity_id,
        campaign_id=campaign_id,
    )
    if not rows:
        return False
    if goal is None:
        return True
    return any(str(r.get("goal") or "") == goal for r in rows)


def reply_chase_hint(
    *,
    identity_id: int,
    campaign_id: str,
    message_id: str,
    thread_id: str | None,
    env: str = "LIVE",
) -> dict[str, Any]:
    """Deterministic follow-up policy for one inbound Gmail message."""
    row = get_reply_draft_row(
        identity_id=identity_id, campaign_id=campaign_id, env=env,
    )
    fact = row.get("value") if isinstance(row, dict) else None
    captured_at = row.get("captured_at") if isinstance(row, dict) else None
    evaluation = reply_chase.evaluate_chase(
        reply_draft_fact=fact if isinstance(fact, dict) else None,
        reply_draft_captured_at=str(captured_at) if captured_at else None,
        inbound_message_id=message_id,
        inbound_thread_id=thread_id,
        event_thread_ids=collect_campaign_thread_ids(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
        ),
    )
    if has_awaiting_escalation(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
    ):
        evaluation = reply_chase.apply_open_escalation_defer(evaluation)
    return {
        **reply_chase.chase_context_from_evaluation(evaluation),
        "recommended_action": evaluation.get("recommended_action"),
    }


def reply_dispatch_status(
    *,
    identity_id: int,
    campaign_id: str,
    message_id: str,
    env: str = "LIVE",
) -> dict[str, Any]:
    """Return poller idempotency hints for one inbound Gmail message."""

    def _do() -> dict[str, Any]:
        with _connect() as conn:
            draft_ready = conn.execute(
                """SELECT id FROM kol_conversation_events
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND event_type='kol_reply_draft_ready'
                      AND json_extract(payload_json,'$.source_message_id')=?
                    LIMIT 1""",
                (identity_id, campaign_id, env, message_id),
            ).fetchone()
            inbound = conn.execute(
                """SELECT id FROM kol_conversation_events
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND event_type='kol_inbound_reply'
                      AND json_extract(payload_json,'$.message_id')=?
                    LIMIT 1""",
                (identity_id, campaign_id, env, message_id),
            ).fetchone()
            fact_row = conn.execute(
                """SELECT fact_value FROM kol_facts_latest
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND fact_key='approval.reply_draft'""",
                (identity_id, campaign_id, env),
            ).fetchone()
            mismatch_esc = conn.execute(
                """SELECT id FROM kol_escalations
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND reason='inbound_mailbox_mismatch'
                      AND state='awaiting_answer'
                      AND json_extract(resume_context_json,'$.source_message_id')=?
                    LIMIT 1""",
                (identity_id, campaign_id, env, message_id),
            ).fetchone()
        has_pending_draft = False
        reply_draft_val: dict[str, Any] | None = None
        if fact_row:
            val = _jl(fact_row["fact_value"], {})
            if isinstance(val, dict):
                reply_draft_val = val
                _, prior_src = reply_draft.extract_thread_anchors(val)
                if prior_src == message_id:
                    has_pending_draft = val.get("decision") in (None, "pending")
        has_draft_ready = draft_ready is not None
        has_inbound = inbound is not None
        has_mailbox_mismatch_esc = mismatch_esc is not None
        chase = reply_chase.evaluate_chase(
            reply_draft_fact=reply_draft_val,
            reply_draft_captured_at=None,
            inbound_message_id=message_id,
            inbound_thread_id=None,
            event_thread_ids=collect_campaign_thread_ids(
                identity_id=identity_id,
                campaign_id=campaign_id,
                env=env,
            ),
        )
        chase_context = reply_chase.chase_context_from_evaluation(chase)
        return {
            "message_id": message_id,
            "has_inbound_event": has_inbound,
            "has_draft_ready_event": has_draft_ready,
            "has_pending_reply_draft": has_pending_draft,
            "has_mailbox_mismatch_escalation": has_mailbox_mismatch_esc,
            "chase_action": chase.get("recommended_action"),
            "chase_context": chase_context,
            "should_skip_poller": bool(
                has_draft_ready or has_pending_draft or has_mailbox_mismatch_esc
            ),
            "should_retry_gateway_only": bool(
                has_inbound
                and not has_draft_ready
                and not has_pending_draft
                and not has_mailbox_mismatch_esc
            ),
        }

    return _safe("reply_dispatch_status", _do) or {
        "message_id": message_id,
        "should_skip_poller": False,
        "should_retry_gateway_only": False,
    }


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
            from . import escalation_inbounds

            ctx: dict[str, Any] = dict(resume_context or {})
            ctx = escalation_inbounds.seed_trigger_inbound(ctx)
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
    tags: Optional[list[str]] = None,
    suggested_fix: Optional[str] = None,
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
                "tags": list(tags or []),
                "suggested_fix": suggested_fix or "",
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


def append_pending_inbound_on_inbound_event(
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    payload: Mapping[str, Any],
    event_id: Optional[int] = None,
    event_ts: Optional[str] = None,
) -> int:
    """Attach a new inbound anchor to open ``awaiting_answer`` escalations.

    Called deterministically when ``kol_inbound_reply`` events are written
    so follow-ups during an open escalation appear in ``pending_inbounds``.
    """
    from . import escalation_inbounds

    def _do() -> int:
        anchor = escalation_inbounds.inbound_anchor_from_payload(
            payload,
            event_id=event_id,
            ts=event_ts,
            role="followup",
        )
        if not anchor:
            return 0
        with _connect() as conn:
            rows = conn.execute(
                """SELECT id, resume_context_json, question_to_operator
                     FROM kol_escalations
                    WHERE identity_id=? AND campaign_id=? AND env=?
                      AND state='awaiting_answer'""",
                (identity_id, campaign_id, env),
            ).fetchall()
            if not rows:
                return 0
            target_ids = set(
                escalation_inbounds.select_escalation_ids_for_followup(
                    rows,
                    anchor,
                    parse_ctx=lambda raw: _jl(raw, {}) or {},
                )
            )
            if not target_ids:
                return 0
            now = _now()
            updated = 0
            for row in rows:
                if int(row["id"]) not in target_ids:
                    continue
                ctx = _jl(row["resume_context_json"], {}) or {}
                merged = escalation_inbounds.append_pending_inbound(ctx, anchor)
                if merged == ctx:
                    continue
                new_question = row["question_to_operator"]
                pending_after = merged.get("pending_inbounds") or []
                last_role = (
                    pending_after[-1].get("role")
                    if pending_after and isinstance(pending_after[-1], dict)
                    else None
                )
                if last_role == "followup":
                    new_question = escalation_inbounds.append_followup_to_suggested_question(
                        row["question_to_operator"],
                        anchor,
                    )
                conn.execute(
                    """UPDATE kol_escalations
                          SET resume_context_json=?, question_to_operator=?,
                              updated_at=?
                        WHERE id=?""",
                    (_j(merged), new_question, now, int(row["id"])),
                )
                updated += 1
            return updated

    return int(_safe("append_pending_inbound_on_inbound_event", _do) or 0)


def sync_escalation_pending_inbounds(
    escalation_id: int,
    *,
    inbound_events: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Backfill ``pending_inbounds`` for legacy inbound-tagged escalations."""
    from . import escalation_inbounds

    def _do() -> dict[str, Any]:
        with _connect() as conn:
            row = conn.execute(
                """SELECT id, identity_id, campaign_id, env, state,
                          created_at, resume_context_json, question_to_operator
                     FROM kol_escalations WHERE id=?""",
                (escalation_id,),
            ).fetchone()
        if not row:
            return {"synced": False, "reason": "not_found"}
        if row["state"] != "awaiting_answer":
            return {"synced": False, "reason": "not_awaiting"}
        ctx = _jl(row["resume_context_json"], {}) or {}
        question_to_operator = row["question_to_operator"]
        if not escalation_inbounds.is_inbound_tagged_resume_context(ctx):
            return {"synced": False, "reason": "not_inbound_tagged"}
        identity_id = int(row["identity_id"] or 0)
        campaign_id = row["campaign_id"]
        env = row["env"] or "LIVE"
        if not identity_id or not campaign_id:
            return {"synced": False, "reason": "missing_scope"}
        events = (
            list(inbound_events)
            if inbound_events is not None
            else list_events(
                env=env,
                identity_id=identity_id,
                campaign_id=campaign_id,
                limit=200,
            )
        )
        if not escalation_inbounds.needs_pending_inbound_sync(
            ctx,
            events,
            escalation_created_at=row["created_at"] or "",
        ):
            return {"synced": False, "reason": "already_populated"}
        inbounds = [
            ev for ev in events
            if isinstance(ev, dict) and ev.get("event_type") == "kol_inbound_reply"
        ]
        if not inbounds:
            return {"synced": False, "reason": "no_inbound_events"}
        created = row["created_at"] or ""
        trigger_ev = None
        for ev in inbounds:
            ts = ev.get("ts") or ""
            if ts and created and ts <= created:
                trigger_ev = ev
                break
        if trigger_ev is None:
            trigger_ev = inbounds[0]
        new_ctx = dict(ctx)
        new_ctx = escalation_inbounds.seed_trigger_inbound(new_ctx)
        trig_payload = (
            trigger_ev.get("payload")
            if isinstance(trigger_ev.get("payload"), dict)
            else {}
        )
        trig_anchor = escalation_inbounds.inbound_anchor_from_payload(
            trig_payload,
            event_id=trigger_ev.get("id"),
            ts=trigger_ev.get("ts"),
            role="trigger",
        )
        if trig_anchor:
            new_ctx = escalation_inbounds.append_pending_inbound(new_ctx, {
                **trig_anchor,
                "role": "trigger",
            })
        for ev in inbounds:
            if ev is trigger_ev:
                continue
            ts = ev.get("ts") or ""
            if created and ts and ts <= created:
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            follow = escalation_inbounds.inbound_anchor_from_payload(
                payload,
                event_id=ev.get("id"),
                ts=ev.get("ts"),
                role="followup",
            )
            if follow:
                new_ctx = escalation_inbounds.append_pending_inbound(new_ctx, follow)
        if new_ctx == ctx:
            return {"synced": False, "reason": "unchanged"}
        old_ids = {
            str(x.get("message_id") or "")
            for x in (ctx.get("pending_inbounds") or [])
            if isinstance(x, dict)
        }
        new_question = question_to_operator
        for anchor in new_ctx.get("pending_inbounds") or []:
            if not isinstance(anchor, dict):
                continue
            if anchor.get("role") != "followup":
                continue
            mid = str(anchor.get("message_id") or "")
            if mid and mid not in old_ids:
                new_question = escalation_inbounds.append_followup_to_suggested_question(
                    new_question,
                    anchor,
                )
        with _connect() as conn:
            conn.execute(
                """UPDATE kol_escalations
                      SET resume_context_json=?, question_to_operator=?,
                          updated_at=?
                    WHERE id=?""",
                (_j(new_ctx), new_question, _now(), escalation_id),
            )
        count = len(new_ctx.get("pending_inbounds") or [])
        return {"synced": True, "pending_inbound_count": count}

    out = _safe("sync_escalation_pending_inbounds", _do)
    return out if isinstance(out, dict) else {"synced": False, "reason": "error"}


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


def get_escalation(escalation_id: int) -> Optional[dict[str, Any]]:
    """Return one escalation row by id with JSON columns decoded, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM kol_escalations WHERE id=?",
            (escalation_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["resume_context"] = _jl(d.pop("resume_context_json", "{}"), {})
    d["operator_facts"] = _jl(d.pop("operator_facts_json", None), None)
    return d


def list_escalations(
    *,
    state: Optional[str] = None,
    env: str = "LIVE",
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    where = ["e.env = ?"]
    args: list[Any] = [env]
    if state:
        where.append("e.state = ?")
        args.append(state)
    if identity_id is not None:
        where.append("e.identity_id = ?")
        args.append(int(identity_id))
    if campaign_id:
        where.append("e.campaign_id = ?")
        args.append(campaign_id)
    sql = (
        "SELECT e.*, i.primary_handle AS primary_handle "
        "FROM kol_escalations e "
        "LEFT JOIN kol_identity i ON i.id = e.identity_id "
        f"WHERE {' AND '.join(where)} ORDER BY e.id DESC"
    )
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        handle = d.get("primary_handle")
        if isinstance(handle, str):
            handle = handle.strip().lstrip("@") or None
        d["handle"] = handle
        d["resume_context"] = _jl(d.pop("resume_context_json", "{}"), {})
        d["operator_facts"] = _jl(d.pop("operator_facts_json", None), None)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Approvals (read-only view over kol_facts)
# ---------------------------------------------------------------------------


def _approval_handle_from_row(row: Mapping[str, Any]) -> Optional[str]:
    handle = row.get("primary_handle")
    if not isinstance(handle, str):
        return None
    handle = handle.strip().lstrip("@")
    return handle or None


def _approval_rows_query(
    *,
    env: str,
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    where = ["f.fact_namespace = 'approval'", "f.env = ?"]
    args: list[Any] = [env]
    if identity_id is not None:
        where.append("f.identity_id = ?")
        args.append(int(identity_id))
    if campaign_id:
        where.append("f.campaign_id = ?")
        args.append(campaign_id)
    sql = (
        "SELECT f.identity_id, f.campaign_id, f.fact_key, f.fact_value, "
        "f.captured_at, i.primary_handle "
        "FROM kol_facts_latest f "
        "LEFT JOIN kol_identity i ON i.id = f.identity_id "
        f"WHERE {' AND '.join(where)} ORDER BY f.id DESC"
    )
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def list_pending_approvals(
    *,
    env: str = "LIVE",
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return latest ``approval.*`` facts that actually need an operator
    decision. A fact is "pending" only when its value is a JSON object
    with no ``decision`` field set yet (or set to ``"pending"``).

    Scalar-valued ``approval.*`` facts (e.g. ``approval.<goal>_terminated
    = true`` from skill 3d, ``approval.next_action_type = "<type>"`` from
    skill 3e) are skill-internal markers consumed by downstream skills,
    not items requiring a console decision, so they are excluded here.
    """
    out = []
    for r in _approval_rows_query(
        env=env, identity_id=identity_id, campaign_id=campaign_id,
    ):
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
                "handle": _approval_handle_from_row(r),
            })
    return out


def list_decided_approvals(
    *,
    status: str,
    env: str = "LIVE",
    identity_id: Optional[int] = None,
    campaign_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return decided ``approval.*`` facts whose ``value.decision``
    matches ``status``. ``status`` must be one of ``approved`` /
    ``rejected`` / ``all`` (``all`` returns both approved and rejected,
    not pending — pending is served by ``list_pending_approvals``).
    """
    if status not in ("approved", "rejected", "all"):
        raise ValueError(f"unknown status: {status!r}")
    out: list[dict[str, Any]] = []
    for r in _approval_rows_query(
        env=env, identity_id=identity_id, campaign_id=campaign_id,
    ):
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
            "handle": _approval_handle_from_row(r),
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


def list_sent_reply_drafts_for_edit_learning(*, env: str = "LIVE") -> list[dict[str, Any]]:
    """Return approved ``approval.reply_draft`` facts already marked sent.

    Used to backfill ``draft_edit_learning`` when an older lightweight
    sent-reconcile path marked ``offer.outreach_sent`` without capturing diffs.
    """
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
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        )
        if facts.get("offer.outreach_sent") is not True:
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
    negotiation_style: Optional[str] = None,
    decided_by: str = "skill:archival-writer",
    env: str = "LIVE",
    run_outcome_retro: bool = True,
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
        negotiation_style=negotiation_style,
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
        env=env,
    )
    if run_outcome_retro:
        try:
            from . import learning_outcome

            with _connect() as conn:
                learning_outcome.analyze_one_collab_outcome(
                    conn,
                    identity_id=identity_id,
                    campaign_id=campaign_id,
                    env=env,
                    outcome=outcome,
                    updated_by=f"archive:{decided_by}",
                )
        except Exception:
            log.warning(
                "Tier1 collab outcome retro failed after archive_collab",
                exc_info=True,
                extra={"identity_id": identity_id, "campaign_id": campaign_id},
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
    "get_candidate_for",
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
    "aggregate_kol_registry_funnel",
    "list_discovered_kol_registry",
    "list_escalations",
    "list_events",
    "list_pending_approvals",
    "list_decided_approvals",
    "list_approved_reply_drafts",
    "list_sent_reply_drafts_for_edit_learning",
    "open_escalation",
    "recompute_goals",
    "reply_dispatch_status",
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
