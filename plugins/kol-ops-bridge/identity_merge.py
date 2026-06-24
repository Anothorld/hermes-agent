"""Merge duplicate ``kol_identity`` rows that share the same handle.

Used when legacy imports created a second identity row for an already-imported
handle. Reassigns dependent rows to ``keep_id`` and deletes ``merge_id``.
"""

from __future__ import annotations

import json
from typing import Any, Final, Optional

try:
    from . import cal  # type: ignore[import-not-found]
except ImportError:
    import cal  # type: ignore[no-redef]

_IDENTITY_TABLES: Final[tuple[str, ...]] = (
    "kol_facts",
    "kol_conversation_events",
    "campaign_candidates",
    "kol_escalations",
    "kol_goal_state",
)

_CANDIDATE_STATUS_RANK: Final[dict[str, int]] = {
    "selected_for_outreach": 50,
    "needs_review": 40,
    "shortlisted": 30,
    "discovered": 20,
    "rejected": 10,
    "archived": 0,
}


def _jl(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return default


def list_duplicate_identity_groups(
    *,
    env: str = "LIVE",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return handle groups with more than one identity row."""
    with cal._connect() as conn:  # noqa: SLF001
        rows = conn.execute(
            """SELECT lower(primary_handle) AS handle_key,
                      group_concat(id) AS ids,
                      group_concat(primary_handle) AS handles,
                      COUNT(*) AS n
                 FROM kol_identity
                WHERE env=?
                GROUP BY platform, lower(primary_handle), env
               HAVING n > 1
                ORDER BY handle_key
                LIMIT ?""",
            (env, int(limit)),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        ids = [int(x) for x in str(row["ids"]).split(",") if x.strip().isdigit()]
        out.append({
            "handle_key": row["handle_key"],
            "identity_ids": sorted(ids),
            "count": int(row["n"]),
        })
    return out


def _pick_keep_id(keep_id: Optional[int], merge_id: int, other_id: int) -> tuple[int, int]:
    if keep_id is not None:
        if merge_id == keep_id:
            return keep_id, other_id
        return keep_id, merge_id
    # Default: keep the lower (older) id — usually holds the richer first import.
    lo, hi = sorted((merge_id, other_id))
    return lo, hi


def _merge_relationship(conn: Any, *, keep_id: int, merge_id: int) -> dict[str, Any]:
    keep = conn.execute(
        "SELECT * FROM kol_relationship WHERE identity_id=?", (keep_id,),
    ).fetchone()
    drop = conn.execute(
        "SELECT * FROM kol_relationship WHERE identity_id=?", (merge_id,),
    ).fetchone()
    if not drop:
        return {"merged": False}
    keep_skus = _jl(keep["preferred_skus_json"] if keep else "[]", [])
    drop_skus = _jl(drop["preferred_skus_json"], [])
    merged_skus = list(dict.fromkeys([*(keep_skus or []), *(drop_skus or [])]))
    total = int((keep["total_collabs"] if keep else 0) or 0) + int(drop["total_collabs"] or 0)
    keep_arch = (keep["last_archived_at"] if keep else None) or ""
    drop_arch = drop["last_archived_at"] or ""
    if drop_arch > keep_arch:
        last_campaign_id = drop["last_campaign_id"]
        last_outcome = drop["last_outcome"]
        last_archived_at = drop_arch
    else:
        last_campaign_id = keep["last_campaign_id"] if keep else drop["last_campaign_id"]
        last_outcome = keep["last_outcome"] if keep else drop["last_outcome"]
        last_archived_at = keep_arch or drop_arch
    now = cal._now()  # noqa: SLF001
    if keep:
        conn.execute(
            """UPDATE kol_relationship SET
                 total_collabs = ?,
                 last_campaign_id = ?,
                 last_outcome = ?,
                 preferred_skus_json = ?,
                 last_archived_at = ?,
                 updated_at = ?
               WHERE identity_id = ?""",
            (
                total,
                last_campaign_id,
                last_outcome,
                json.dumps(merged_skus, ensure_ascii=False),
                last_archived_at or None,
                now,
                keep_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO kol_relationship
               (identity_id, total_collabs, last_campaign_id, last_outcome,
                preferred_skus_json, preferred_mode, last_archived_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                keep_id,
                total,
                last_campaign_id,
                last_outcome,
                json.dumps(merged_skus, ensure_ascii=False),
                drop["preferred_mode"] or "unknown",
                last_archived_at or None,
                now,
            ),
        )
    conn.execute("DELETE FROM kol_relationship WHERE identity_id=?", (merge_id,))
    return {
        "merged": True,
        "total_collabs": total,
        "last_outcome": last_outcome,
        "last_campaign_id": last_campaign_id,
    }


def _resolve_candidate_conflict(conn: Any, *, keep_id: int, merge_id: int) -> int:
    rows = conn.execute(
        """SELECT id, campaign_id, env, candidate_status
             FROM campaign_candidates
            WHERE identity_id=?""",
        (merge_id,),
    ).fetchall()
    deleted = 0
    for row in rows:
        existing = conn.execute(
            """SELECT id, candidate_status FROM campaign_candidates
                WHERE identity_id=? AND campaign_id=? AND env=?""",
            (keep_id, row["campaign_id"], row["env"]),
        ).fetchone()
        if not existing:
            conn.execute(
                "UPDATE campaign_candidates SET identity_id=? WHERE id=?",
                (keep_id, row["id"]),
            )
            continue
        keep_rank = _CANDIDATE_STATUS_RANK.get(existing["candidate_status"], 0)
        merge_rank = _CANDIDATE_STATUS_RANK.get(row["candidate_status"], 0)
        if merge_rank > keep_rank:
            conn.execute("DELETE FROM campaign_candidates WHERE id=?", (existing["id"],))
            conn.execute(
                "UPDATE campaign_candidates SET identity_id=? WHERE id=?",
                (keep_id, row["id"]),
            )
        else:
            conn.execute("DELETE FROM campaign_candidates WHERE id=?", (row["id"],))
        deleted += 1
    return deleted


def _resolve_goal_state_conflict(conn: Any, *, keep_id: int, merge_id: int) -> int:
    rows = conn.execute(
        """SELECT campaign_id, goal, env, updated_at
             FROM kol_goal_state WHERE identity_id=?""",
        (merge_id,),
    ).fetchall()
    dropped = 0
    for row in rows:
        existing = conn.execute(
            """SELECT updated_at FROM kol_goal_state
                WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
            (keep_id, row["campaign_id"], row["goal"], row["env"]),
        ).fetchone()
        if not existing:
            conn.execute(
                """UPDATE kol_goal_state SET identity_id=?
                    WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                (keep_id, merge_id, row["campaign_id"], row["goal"], row["env"]),
            )
            continue
        if (row["updated_at"] or "") > (existing["updated_at"] or ""):
            conn.execute(
                """DELETE FROM kol_goal_state
                    WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                (keep_id, row["campaign_id"], row["goal"], row["env"]),
            )
            conn.execute(
                """UPDATE kol_goal_state SET identity_id=?
                    WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                (keep_id, merge_id, row["campaign_id"], row["goal"], row["env"]),
            )
        else:
            conn.execute(
                """DELETE FROM kol_goal_state
                    WHERE identity_id=? AND campaign_id=? AND goal=? AND env=?""",
                (merge_id, row["campaign_id"], row["goal"], row["env"]),
            )
        dropped += 1
    return dropped


def merge_identities(
    *,
    keep_id: int,
    merge_id: int,
    env: str = "LIVE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move ``merge_id`` rows onto ``keep_id`` and delete the duplicate identity."""
    if keep_id == merge_id:
        raise ValueError("keep_id and merge_id must differ")

    keep = cal.get_identity(keep_id)
    drop = cal.get_identity(merge_id)
    if not keep or not drop:
        raise ValueError("keep_id and merge_id must both exist")
    if keep.get("env") != env or drop.get("env") != env:
        raise ValueError(f"both identities must be env={env}")
    if keep.get("primary_handle", "").lower() != drop.get("primary_handle", "").lower():
        raise ValueError("identities must share the same primary_handle")
    result_platform_note: dict[str, str] | None = None
    if keep.get("platform") != drop.get("platform"):
        result_platform_note = {
            "keep_platform": str(keep.get("platform") or ""),
            "merge_platform": str(drop.get("platform") or ""),
        }
        # Prefer instagram when merging legacy duplicate rows (daily report is canonical).
        preferred = "instagram"
        platforms = {keep.get("platform"), drop.get("platform")}
        if preferred in platforms:
            with cal._connect() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE kol_identity SET platform=?, updated_at=? WHERE id=?",
                    (preferred, cal._now(), keep_id),  # noqa: SLF001
                )
                conn.commit()
            result_platform_note["canonical_platform"] = preferred

    counts_before: dict[str, int] = {}
    with cal._connect() as conn:  # noqa: SLF001
        for table in _IDENTITY_TABLES:
            counts_before[table] = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE identity_id=?",
                    (merge_id,),
                ).fetchone()[0]
            )

    result: dict[str, Any] = {
        "env": env,
        "dry_run": dry_run,
        "keep_id": keep_id,
        "merge_id": merge_id,
        "handle": keep.get("primary_handle"),
        "rows_to_move": counts_before,
    }
    if result_platform_note:
        result["platform_mismatch"] = result_platform_note
    if dry_run:
        return result

    moved: dict[str, int] = {}
    with cal._connect() as conn:  # noqa: SLF001
        cand_conflicts = _resolve_candidate_conflict(conn, keep_id=keep_id, merge_id=merge_id)
        goal_conflicts = _resolve_goal_state_conflict(conn, keep_id=keep_id, merge_id=merge_id)
        for table in ("kol_facts", "kol_conversation_events", "kol_escalations"):
            cur = conn.execute(
                f"UPDATE {table} SET identity_id=? WHERE identity_id=?",
                (keep_id, merge_id),
            )
            moved[table] = int(cur.rowcount)
        moved["campaign_candidates"] = counts_before["campaign_candidates"]
        moved["kol_goal_state"] = counts_before["kol_goal_state"]
        rel = _merge_relationship(conn, keep_id=keep_id, merge_id=merge_id)
        conn.execute("DELETE FROM kol_identity WHERE id=?", (merge_id,))
        conn.commit()

    result["moved"] = moved
    result["candidate_conflicts_resolved"] = cand_conflicts
    result["goal_state_conflicts_resolved"] = goal_conflicts
    result["relationship"] = rel
    result["deleted_identity_id"] = merge_id
    return result


def merge_duplicate_identity_group(
    *,
    identity_ids: list[int],
    keep_id: Optional[int] = None,
    env: str = "LIVE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge every other id in ``identity_ids`` into one canonical row."""
    if len(identity_ids) < 2:
        raise ValueError("identity_ids must contain at least two ids")
    ordered = sorted(set(int(x) for x in identity_ids))
    canonical, first_drop = _pick_keep_id(keep_id, ordered[0], ordered[1])
    merges = [i for i in ordered if i != canonical]
    results: list[dict[str, Any]] = []
    for mid in merges:
        results.append(
            merge_identities(
                keep_id=canonical,
                merge_id=mid,
                env=env,
                dry_run=dry_run,
            )
        )
    return {
        "env": env,
        "dry_run": dry_run,
        "keep_id": canonical,
        "merged_ids": merges,
        "results": results,
    }
