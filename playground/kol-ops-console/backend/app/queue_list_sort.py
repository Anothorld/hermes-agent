"""Shared list sorting for operator queue endpoints (approvals, escalations)."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable

from fastapi import HTTPException, status

VALID_QUEUE_SORT = frozenset({"priority", "time"})
VALID_QUEUE_ORDER = frozenset({"asc", "desc"})


def parse_iso_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def validate_queue_sort(sort: str, order: str) -> None:
    if sort not in VALID_QUEUE_SORT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown sort: {sort} (expected priority|time)",
        )
    if order not in VALID_QUEUE_ORDER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown order: {order} (expected asc|desc)",
        )


def sort_queue_rows(
    rows: list[dict[str, Any]],
    *,
    sort: str,
    order: str,
    time_field: str,
    priority_key: Callable[[dict[str, Any]], tuple],
) -> None:
    """Sort rows in place: ``priority`` (default scoring) or ``time``."""
    if sort == "time":
        reverse = order != "asc"
        rows.sort(
            key=lambda r: parse_iso_ts(r.get(time_field))
            or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc),
            reverse=reverse,
        )
        return
    rows.sort(key=priority_key)
