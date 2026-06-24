"""Repair mis-tagged legacy import outcomes that block discovery skip.

Historical ``红人日报表`` imports could land as ``last_outcome=incomplete``
when spreadsheet metadata (e.g. ``是否凹槽系列=否``) tripped the import
heuristic. Those rows represent signed collabs with cost and should be
``success`` so discovery skip applies.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, Optional

try:
    from . import cal  # type: ignore[import-not-found]
except ImportError:
    import cal  # type: ignore[no-redef]

REPAIR_TARGET_OUTCOME: Final[str] = "success"
DAILY_REPORT_SECTION: Final[str] = "红人日报表"
LEGACY_CAMPAIGN_PREFIX: Final[str] = "legacy-redlist-"


def _jl(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return default


def _history_notes(entry: dict[str, Any]) -> str:
    return str(entry.get("notes") or "")


def is_misclassified_legacy_incomplete_entry(entry: dict[str, Any]) -> bool:
    """True when one collab_history row is a false ``incomplete`` legacy import."""
    if entry.get("outcome") != "incomplete":
        return False
    if entry.get("source") != "legacy_import":
        return False
    notes = _history_notes(entry)
    if f"source_section={DAILY_REPORT_SECTION}" in notes:
        if entry.get("skus") or "product=" in notes:
            return True
    campaign_id = str(entry.get("campaign_id") or "")
    if campaign_id.startswith(LEGACY_CAMPAIGN_PREFIX):
        if entry.get("skus") or "product=" in notes:
            return True
    return False


def is_misclassified_legacy_incomplete(*, identity_id: int) -> bool:
    """True when relationship ``incomplete`` should be upgraded to ``success``."""
    rel = cal.get_relationship(identity_id) or {}
    if rel.get("last_outcome") != "incomplete":
        return False
    if int(rel.get("total_collabs") or 0) <= 0:
        return False
    history = rel.get("collab_history") or cal.list_collab_history(identity_id)
    return any(is_misclassified_legacy_incomplete_entry(h) for h in history)


def should_skip_misclassified_legacy(*, identity_id: int) -> bool:
    """Discovery skip defense before CAL backfill runs."""
    return is_misclassified_legacy_incomplete(identity_id=identity_id)


def list_misclassified_identities(
    *,
    env: str = "LIVE",
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Scan archived KOLs whose ``last_outcome`` is a false ``incomplete``."""
    items: list[dict[str, Any]] = []
    offset = 0
    page = 500
    while len(items) < limit:
        batch = cal.list_archived_kols(
            env=env,
            last_outcome="incomplete",
            limit=page,
            offset=offset,
        )
        rows = batch.get("items") or []
        if not rows:
            break
        for row in rows:
            iid = row.get("identity_id")
            if not isinstance(iid, int):
                continue
            if not is_misclassified_legacy_incomplete(identity_id=iid):
                continue
            items.append({
                "identity_id": iid,
                "handle": row.get("primary_handle"),
                "last_campaign_id": row.get("last_campaign_id"),
                "current_outcome": row.get("last_outcome"),
                "repair_to": REPAIR_TARGET_OUTCOME,
            })
            if len(items) >= limit:
                break
        if len(rows) < page:
            break
        offset += page
    return items


def _update_legacy_event_outcomes(
    conn: Any,
    *,
    identity_id: int,
    new_outcome: str,
) -> int:
    rows = conn.execute(
        """SELECT id, payload_json
             FROM kol_conversation_events
            WHERE identity_id=? AND event_type='legacy.collab_imported'""",
        (identity_id,),
    ).fetchall()
    updated = 0
    for row in rows:
        payload = _jl(row["payload_json"], {}) or {}
        if payload.get("outcome") != "incomplete":
            continue
        section = str(payload.get("source_section") or "")
        has_product = bool(payload.get("product") or payload.get("skus"))
        if section != DAILY_REPORT_SECTION and not has_product:
            continue
        payload["outcome"] = new_outcome
        conn.execute(
            "UPDATE kol_conversation_events SET payload_json=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["id"]),
        )
        updated += 1
    return updated


def list_stale_legacy_event_payloads(
    *,
    env: str = "LIVE",
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Legacy import events still tagged ``incomplete`` after relationship repair."""
    with cal._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            """SELECT e.id AS event_id,
                      e.identity_id AS identity_id,
                      i.primary_handle AS handle,
                      r.last_outcome AS relationship_outcome,
                      json_extract(e.payload_json, '$.outcome') AS event_outcome,
                      json_extract(e.payload_json, '$.source_section') AS source_section
                 FROM kol_conversation_events e
                 JOIN kol_identity i ON i.id = e.identity_id
                 LEFT JOIN kol_relationship r ON r.identity_id = e.identity_id
                WHERE e.env = ?
                  AND e.event_type = 'legacy.collab_imported'
                  AND json_extract(e.payload_json, '$.outcome') = 'incomplete'
                ORDER BY e.id
                LIMIT ?""",
            (env, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def sync_stale_legacy_event_payloads(
    *,
    env: str = "LIVE",
    dry_run: bool = False,
    target_outcome: str = REPAIR_TARGET_OUTCOME,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Align stale ``legacy.collab_imported`` event payloads with repaired outcomes."""
    stale = list_stale_legacy_event_payloads(env=env, limit=limit)
    updated_rows: list[dict[str, Any]] = []
    if dry_run:
        return {
            "env": env,
            "dry_run": True,
            "target_outcome": target_outcome,
            "stale_count": len(stale),
            "items": stale,
        }

    events_updated = 0
    with cal._connect() as conn:  # noqa: SLF001
        for row in stale:
            event_id = int(row["event_id"])
            cur = conn.execute(
                "SELECT payload_json FROM kol_conversation_events WHERE id=?",
                (event_id,),
            ).fetchone()
            if not cur:
                continue
            payload = _jl(cur["payload_json"], {}) or {}
            if payload.get("outcome") != "incomplete":
                continue
            payload["outcome"] = target_outcome
            conn.execute(
                "UPDATE kol_conversation_events SET payload_json=? WHERE id=?",
                (
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    event_id,
                ),
            )
            events_updated += 1
            updated_rows.append({
                "event_id": event_id,
                "identity_id": row.get("identity_id"),
                "handle": row.get("handle"),
                "from_outcome": "incomplete",
                "to_outcome": target_outcome,
                "relationship_outcome": row.get("relationship_outcome"),
                "source_section": row.get("source_section"),
            })
        conn.commit()

    return {
        "env": env,
        "dry_run": False,
        "target_outcome": target_outcome,
        "stale_count": len(stale),
        "events_updated": events_updated,
        "updated": updated_rows,
    }


def repair_identity_outcome(
    *,
    identity_id: int,
    env: str = "LIVE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upgrade one misclassified legacy ``incomplete`` to ``success``."""
    ident = cal.get_identity(identity_id) or {}
    rel = cal.get_relationship(identity_id) or {}
    if not is_misclassified_legacy_incomplete(identity_id=identity_id):
        return {
            "identity_id": identity_id,
            "handle": ident.get("primary_handle"),
            "skipped": True,
            "reason": "not_misclassified",
        }

    campaign_id = rel.get("last_campaign_id")
    result: dict[str, Any] = {
        "identity_id": identity_id,
        "handle": ident.get("primary_handle"),
        "campaign_id": campaign_id,
        "from_outcome": rel.get("last_outcome"),
        "to_outcome": REPAIR_TARGET_OUTCOME,
        "dry_run": dry_run,
        "skipped": False,
    }
    if dry_run:
        return result

    cal.upsert_relationship(
        identity_id=identity_id,
        last_outcome=REPAIR_TARGET_OUTCOME,
        increment_collabs=False,
    )
    events_updated = 0
    with cal._connect() as conn:  # noqa: SLF001 — maintenance path
        events_updated = _update_legacy_event_outcomes(
            conn,
            identity_id=identity_id,
            new_outcome=REPAIR_TARGET_OUTCOME,
        )
        conn.commit()
    result["legacy_events_updated"] = events_updated

    if isinstance(campaign_id, str) and campaign_id.strip():
        cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace="approval",
            facts={"approval.archival_outcome": REPAIR_TARGET_OUTCOME},
            source="maintenance:legacy-outcome-repair",
            env=env,
        )
    return result


def repair_legacy_outcomes(
    *,
    env: str = "LIVE",
    dry_run: bool = False,
    identity_ids: Optional[list[int]] = None,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Batch repair for misclassified legacy daily-report outcomes."""
    if identity_ids:
        targets = [{"identity_id": iid} for iid in identity_ids]
    else:
        targets = list_misclassified_identities(env=env, limit=limit)

    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in targets:
        iid = int(row["identity_id"])
        one = repair_identity_outcome(identity_id=iid, env=env, dry_run=dry_run)
        if one.get("skipped"):
            skipped.append(one)
        else:
            repaired.append(one)

    return {
        "env": env,
        "dry_run": dry_run,
        "scanned": len(targets),
        "repaired_count": len(repaired),
        "skipped_count": len(skipped),
        "repaired": repaired,
        "skipped": skipped,
    }
