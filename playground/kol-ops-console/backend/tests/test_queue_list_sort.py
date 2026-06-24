"""Unit tests for queue list sorting helpers."""

from __future__ import annotations

import datetime as dt

from app.queue_list_sort import parse_iso_ts, sort_queue_rows


def _row(opened_at: str | None) -> dict:
    return {"opened_at": opened_at}


def test_sort_queue_rows_time_asc_oldest_first():
    rows = [
        _row("2026-06-22T12:00:00Z"),
        _row("2026-06-22T10:00:00Z"),
        _row("2026-06-22T11:00:00Z"),
    ]
    sort_queue_rows(
        rows,
        sort="time",
        order="asc",
        time_field="opened_at",
        priority_key=lambda _r: (0, 0),
    )
    assert [r["opened_at"] for r in rows] == [
        "2026-06-22T10:00:00Z",
        "2026-06-22T11:00:00Z",
        "2026-06-22T12:00:00Z",
    ]


def test_sort_queue_rows_time_desc_newest_first():
    rows = [
        _row("2026-06-22T10:00:00Z"),
        _row("2026-06-22T12:00:00Z"),
    ]
    sort_queue_rows(
        rows,
        sort="time",
        order="desc",
        time_field="opened_at",
        priority_key=lambda _r: (0, 0),
    )
    assert rows[0]["opened_at"] == "2026-06-22T12:00:00Z"


def test_sort_queue_rows_priority_uses_scoring():
    rows = [{"score": 2}, {"score": 1}]
    sort_queue_rows(
        rows,
        sort="priority",
        order="asc",
        time_field="opened_at",
        priority_key=lambda r: (r["score"], 0),
    )
    assert [r["score"] for r in rows] == [1, 2]


def test_parse_iso_ts_handles_z_suffix():
    parsed = parse_iso_ts("2026-06-22T10:00:00Z")
    assert parsed == dt.datetime(2026, 6, 22, 10, 0, tzinfo=dt.timezone.utc)
