"""Per-campaign supplement search call budget (``nox_supplement_max_calls``)."""

from __future__ import annotations

from typing import Optional

from internal.locks import file_lock
from internal.nox_cache import _connect, current_cache_month


class SupplementQuotaExceededError(RuntimeError):
    """Campaign supplement search budget exhausted."""


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_supplement (
            cache_month TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            committed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (cache_month, campaign_id)
        )
        """
    )


def _get_row(conn, month: str, campaign_id: str) -> int:
    _ensure_table(conn)
    conn.execute(
        "INSERT OR IGNORE INTO campaign_supplement (cache_month, campaign_id, committed) "
        "VALUES (?, ?, 0)",
        (month, campaign_id),
    )
    row = conn.execute(
        "SELECT committed FROM campaign_supplement "
        "WHERE cache_month = ? AND campaign_id = ?",
        (month, campaign_id),
    ).fetchone()
    return int(row["committed"]) if row else 0


def assert_supplement_allowed(
    campaign_id: str,
    *,
    max_calls: int,
    cache_month: Optional[str] = None,
) -> None:
    """Raise if this campaign has reached supplement cap for the month."""
    if max_calls <= 0:
        raise SupplementQuotaExceededError(
            f"nox_supplement_max_calls={max_calls} blocks supplement search"
        )
    month = cache_month or current_cache_month()
    with file_lock(f"supplement_{campaign_id}"):
        with _connect() as conn:
            used = _get_row(conn, month, campaign_id)
    if used >= max_calls:
        raise SupplementQuotaExceededError(
            f"supplement budget exhausted for {campaign_id}: "
            f"{used}/{max_calls} in {month}"
        )


def commit_supplement(
    campaign_id: str,
    calls: int = 1,
    *,
    cache_month: Optional[str] = None,
) -> int:
    """Record supplement API usage; returns new committed total."""
    month = cache_month or current_cache_month()
    with file_lock(f"supplement_{campaign_id}"):
        with _connect() as conn:
            _get_row(conn, month, campaign_id)
            conn.execute(
                "UPDATE campaign_supplement SET committed = committed + ? "
                "WHERE cache_month = ? AND campaign_id = ?",
                (calls, month, campaign_id),
            )
            conn.commit()
            return _get_row(conn, month, campaign_id)


def supplement_snapshot(
    campaign_id: str,
    *,
    max_calls: int,
    cache_month: Optional[str] = None,
) -> dict:
    month = cache_month or current_cache_month()
    with _connect() as conn:
        used = _get_row(conn, month, campaign_id)
    return {
        "cache_month": month,
        "campaign_id": campaign_id,
        "committed": used,
        "max_calls": max_calls,
        "remaining": max(0, max_calls - used),
    }
