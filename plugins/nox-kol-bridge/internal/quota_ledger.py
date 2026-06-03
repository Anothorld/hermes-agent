"""Monthly API call budget with reserve/commit."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from schemas import DEFAULT_MONTHLY_BUDGET
from internal.locks import file_lock
from internal.nox_cache import _connect, current_cache_month  # reuse db


def _ensure_usage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_ledger (
            cache_month TEXT PRIMARY KEY,
            reserved INTEGER NOT NULL DEFAULT 0,
            committed INTEGER NOT NULL DEFAULT 0,
            monthly_budget INTEGER NOT NULL
        )
        """
    )


def _get_row(conn: sqlite3.Connection, cache_month: str, budget: int) -> sqlite3.Row:
    _ensure_usage_table(conn)
    conn.execute(
        "INSERT OR IGNORE INTO usage_ledger (cache_month, reserved, committed, monthly_budget) "
        "VALUES (?, 0, 0, ?)",
        (cache_month, budget),
    )
    return conn.execute(
        "SELECT * FROM usage_ledger WHERE cache_month = ?", (cache_month,)
    ).fetchone()


class QuotaExceededError(RuntimeError):
    """Local monthly budget exhausted."""


def reserve(
    estimated_calls: int,
    *,
    monthly_budget: int = DEFAULT_MONTHLY_BUDGET,
    cache_month: Optional[str] = None,
) -> None:
    """Reserve slots before CLI (raises if over budget)."""
    month = cache_month or current_cache_month()
    with file_lock("quota"):
        with _connect() as conn:
            row = _get_row(conn, month, monthly_budget)
            if row["committed"] + row["reserved"] + estimated_calls > row["monthly_budget"]:
                raise QuotaExceededError(
                    f"nox monthly budget exceeded: "
                    f"committed={row['committed']} reserved={row['reserved']} "
                    f"need={estimated_calls} budget={row['monthly_budget']}"
                )
            conn.execute(
                "UPDATE usage_ledger SET reserved = reserved + ? WHERE cache_month = ?",
                (estimated_calls, month),
            )
            conn.commit()


def commit(
    actual_calls: int,
    reserved: int,
    *,
    monthly_budget: int = DEFAULT_MONTHLY_BUDGET,
    cache_month: Optional[str] = None,
) -> None:
    """Move reserved to committed after successful CLI."""
    month = cache_month or current_cache_month()
    with file_lock("quota"):
        with _connect() as conn:
            _get_row(conn, month, monthly_budget)
            conn.execute(
                "UPDATE usage_ledger SET "
                "reserved = MAX(0, reserved - ?), "
                "committed = committed + ? "
                "WHERE cache_month = ?",
                (reserved, actual_calls, month),
            )
            conn.commit()


def release(reserved: int, *, cache_month: Optional[str] = None) -> None:
    """Release reservation on failure."""
    month = cache_month or current_cache_month()
    with file_lock("quota"):
        with _connect() as conn:
            conn.execute(
                "UPDATE usage_ledger SET reserved = MAX(0, reserved - ?) "
                "WHERE cache_month = ?",
                (reserved, month),
            )
            conn.commit()


def reconcile_committed_floor(
    remote_used: int,
    *,
    monthly_budget: int = DEFAULT_MONTHLY_BUDGET,
    cache_month: Optional[str] = None,
) -> dict[str, int]:
    """Raise local ``committed`` to at least ``remote_used`` (supplier truth)."""
    month = cache_month or current_cache_month()
    with file_lock("quota"):
        with _connect() as conn:
            row = _get_row(conn, month, monthly_budget)
            current = int(row["committed"])
            target = min(monthly_budget, max(current, int(remote_used)))
            if target > current:
                conn.execute(
                    "UPDATE usage_ledger SET committed = ? WHERE cache_month = ?",
                    (target, month),
                )
                conn.commit()
            row = _get_row(conn, month, monthly_budget)
    return {
        "committed_before": current,
        "committed_after": int(row["committed"]),
        "remote_used": int(remote_used),
    }


def snapshot(
    *,
    monthly_budget: int = DEFAULT_MONTHLY_BUDGET,
    cache_month: Optional[str] = None,
) -> dict:
    month = cache_month or current_cache_month()
    with _connect() as conn:
        row = _get_row(conn, month, monthly_budget)
    remaining = row["monthly_budget"] - row["committed"] - row["reserved"]
    return {
        "cache_month": month,
        "committed": row["committed"],
        "reserved": row["reserved"],
        "monthly_budget": row["monthly_budget"],
        "remaining_estimate": max(0, remaining),
    }
