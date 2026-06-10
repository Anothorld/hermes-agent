"""Dynamic vocabulary for shortlist-decision learning tags.

Unlike :mod:`reject_tags` (frozen set), this vocabulary is stored in CAL
(``discovery_decision_tags``) so the nightly mining job can propose new tags
from high-frequency operator comments and operators can approve them in the
Console learning page. Seed tags are inserted idempotently on first use.

Tag rows:

* ``action_scope`` — which shortlist action the tag applies to
  (``approve`` | ``remove`` | ``transfer`` | ``any``).
* ``status`` — ``active`` (selectable in UI), ``proposed`` (mined, awaiting
  operator approval), ``rejected`` (mined and declined; kept to avoid
  re-proposing).
* ``source`` — ``seed`` | ``mined`` | ``operator``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import sqlite3
from typing import Any, Final, Optional

log = logging.getLogger(__name__)

DECISION_ACTIONS: Final[tuple[str, ...]] = ("approve", "remove", "transfer")

DEFAULT_DECISION_TAG: Final[str] = "other"

_TAG_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{1,63}$")

# (tag, label_zh, action_scope)
SEED_TAGS: Final[tuple[tuple[str, str, str], ...]] = (
    # Negative — remove from shortlist
    ("tone_mismatch", "调性不符", "remove"),
    ("fake_followers", "粉丝水分高", "remove"),
    ("competitor_bound", "竞品绑定深", "remove"),
    ("over_budget", "报价超预算", "remove"),
    ("visual_style_mismatch", "视觉风格不匹配", "remove"),
    ("audience_mismatch", "受众画像不符", "remove"),
    ("low_engagement", "互动质量差", "remove"),
    ("content_quality_low", "内容质量不足", "remove"),
    # Positive — approve shortlist
    ("tone_match", "调性契合", "approve"),
    ("audience_fit", "受众精准", "approve"),
    ("content_quality_high", "内容质量高", "approve"),
    ("visual_style_match", "视觉风格匹配", "approve"),
    ("price_reasonable", "报价合理", "approve"),
    ("strong_engagement", "互动数据好", "approve"),
    # Transfer — move to another campaign
    ("better_fit_other_product", "更适合其他产品", "transfer"),
    ("timing_mismatch", "档期/时机不合", "transfer"),
    ("budget_fit_other_campaign", "预算更适合其他活动", "transfer"),
    # Catch-all
    (DEFAULT_DECISION_TAG, "其他", "any"),
)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def ensure_seed_tags(conn: sqlite3.Connection) -> int:
    """Insert seed tags idempotently; returns number of rows inserted."""
    now = _now()
    inserted = 0
    for tag, label_zh, action_scope in SEED_TAGS:
        cur = conn.execute(
            """INSERT OR IGNORE INTO discovery_decision_tags
                   (tag, label_zh, action_scope, status, source,
                    evidence_json, created_at, updated_at)
               VALUES (?, ?, ?, 'active', 'seed', '[]', ?, ?)""",
            (tag, label_zh, action_scope, now, now),
        )
        inserted += cur.rowcount or 0
    conn.commit()
    return inserted


def list_tags(
    conn: sqlite3.Connection,
    *,
    action: Optional[str] = None,
    status: str = "active",
) -> list[dict[str, Any]]:
    """Return tag rows; ``action`` filters by scope (scope ``any`` always included)."""
    ensure_seed_tags(conn)
    where = ["status = ?"]
    args: list[Any] = [status]
    if action:
        if action not in DECISION_ACTIONS:
            raise ValueError(f"unknown decision action: {action!r}")
        where.append("(action_scope = ? OR action_scope = 'any')")
        args.append(action)
    rows = conn.execute(
        "SELECT tag, label_zh, action_scope, status, source, evidence_json, "
        "created_at, updated_at FROM discovery_decision_tags "
        f"WHERE {' AND '.join(where)} ORDER BY source = 'seed' DESC, tag",
        args,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        except (TypeError, ValueError):
            d["evidence"] = []
        out.append(d)
    return out


def active_tag_set(conn: sqlite3.Connection, *, action: Optional[str] = None) -> set[str]:
    return {row["tag"] for row in list_tags(conn, action=action, status="active")}


def normalize_decision_tags(
    conn: sqlite3.Connection,
    raw: Optional[list[str]],
    *,
    action: Optional[str] = None,
) -> list[str]:
    """Return deduplicated valid active tags; unknown values map to ``other``."""
    valid = active_tag_set(conn, action=action)
    if not raw:
        return [DEFAULT_DECISION_TAG]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = str(item or "").strip().lower()
        if not tag:
            continue
        if tag not in valid:
            tag = DEFAULT_DECISION_TAG
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out or [DEFAULT_DECISION_TAG]


def propose_tag(
    conn: sqlite3.Connection,
    *,
    tag: str,
    label_zh: str,
    action_scope: str = "any",
    evidence: Optional[list[str]] = None,
    source: str = "mined",
) -> dict[str, Any]:
    """Insert a ``proposed`` tag row; no-op when the tag already exists.

    Returns ``{"created": bool, "tag": tag}``. Existing rows (any status,
    including ``rejected``) are never overwritten so a declined mining
    proposal is not re-surfaced every night.
    """
    slug = str(tag or "").strip().lower()
    if not _TAG_SLUG.match(slug):
        raise ValueError(f"invalid tag slug: {tag!r}")
    if action_scope not in (*DECISION_ACTIONS, "any"):
        raise ValueError(f"invalid action_scope: {action_scope!r}")
    now = _now()
    cur = conn.execute(
        """INSERT OR IGNORE INTO discovery_decision_tags
               (tag, label_zh, action_scope, status, source,
                evidence_json, created_at, updated_at)
           VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)""",
        (
            slug,
            str(label_zh or slug).strip()[:80],
            action_scope,
            source,
            json.dumps([str(e)[:200] for e in (evidence or [])][:10], ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    return {"created": bool(cur.rowcount), "tag": slug}


def decide_proposed_tag(
    conn: sqlite3.Connection,
    *,
    tag: str,
    decision: str,
) -> dict[str, Any]:
    """Approve (→ active) or reject a ``proposed`` tag."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be approved|rejected, got {decision!r}")
    new_status = "active" if decision == "approved" else "rejected"
    cur = conn.execute(
        """UPDATE discovery_decision_tags
              SET status = ?, updated_at = ?
            WHERE tag = ? AND status = 'proposed'""",
        (new_status, _now(), str(tag or "").strip().lower()),
    )
    conn.commit()
    if not cur.rowcount:
        raise ValueError(f"no proposed tag {tag!r} to decide")
    return {"tag": tag, "status": new_status}


__all__ = [
    "DECISION_ACTIONS",
    "DEFAULT_DECISION_TAG",
    "SEED_TAGS",
    "active_tag_set",
    "decide_proposed_tag",
    "ensure_seed_tags",
    "list_tags",
    "normalize_decision_tags",
    "propose_tag",
]
