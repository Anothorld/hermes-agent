"""Unit tests for the gateway-approval watcher.

The watcher subscribes to one or more upstream SSE streams (one per
active run_id) and broadcasts ``gateway_approval.*`` events.  We test
its handling of the inner-event payloads in isolation by feeding the
``_handle_frame`` parser directly, plus a small end-to-end driver that
exercises the SSE consumption loop against a stubbed gateway.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest

pytest.importorskip("fastapi")

from app.gateway_approval_watcher import (  # noqa: E402
    GatewayApprovalWatcher,
    _UnrecoverableUpstream,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _seed_db(tmp_path: Path, *, runs: list[tuple[str, str, str]]) -> Path:
    db = tmp_path / "watcher.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            session_id TEXT,
            dedup_key TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )"""
    )
    now = _utcnow()
    for run_id, campaign_id, kind in runs:
        conn.execute(
            "INSERT INTO product_campaign_runs "
            "(campaign_id,env,run_id,kind,started_at) VALUES (?,?,?,?,?)",
            (campaign_id, "TEST", run_id, kind, now),
        )
    conn.commit()
    conn.close()
    return db


def _make_settings(*, db_path: Path, gateway_base: str = "http://gw.invalid"):
    """Build a duck-typed settings object: the watcher only reads
    ``db_path``, ``gateway_base``, ``gateway_key``.
    """
    class _S:
        pass
    s = _S()
    s.db_path = db_path
    s.gateway_base = gateway_base
    s.gateway_key = ""
    return s


# ---------------------------------------------------------------------------
# _handle_frame: stream → state transitions
# ---------------------------------------------------------------------------


def test_handle_frame_approval_request_populates_pending() -> None:
    w = GatewayApprovalWatcher()
    payload = json.dumps({
        "event": "approval.request",
        "run_id": "r1",
        "command": "rm -rf /tmp/foo",
        "description": "recursive delete",
        "pattern_key": "rm-rf",
        "pattern_keys": ["rm-rf"],
        "choices": ["once", "session", "always", "deny"],
        "timestamp": 1700000000,
    })
    q = w.subscribe()
    terminal = w._handle_frame(
        run_id="r1", kind="outreach", campaign_id="CID-1",
        event_name="approval.request", data_str=payload,
    )
    assert terminal is False
    snap, seq = w.snapshot()
    assert len(snap) == 1 and snap[0]["run_id"] == "r1"
    assert snap[0]["command"] == "rm -rf /tmp/foo"
    assert snap[0]["campaign_id"] == "CID-1"
    assert snap[0]["kind"] == "outreach"
    assert snap[0]["source"] == "gateway"
    assert seq == 1
    ev = q.get_nowait()
    assert ev["event"] == "gateway_approval.request"
    assert ev["run_id"] == "r1"
    assert ev["seq"] == 1


def test_handle_frame_responded_emits_choice_and_clears() -> None:
    w = GatewayApprovalWatcher()
    # Seed a pending entry first.
    w._handle_frame(
        run_id="r1", kind="outreach", campaign_id="CID-1",
        event_name="approval.request",
        data_str=json.dumps({"command": "x", "description": "y"}),
    )
    q = w.subscribe()
    terminal = w._handle_frame(
        run_id="r1", kind="outreach", campaign_id="CID-1",
        event_name="approval.responded",
        data_str=json.dumps({"choice": "once", "resolved": 1}),
    )
    assert terminal is False
    snap, _ = w.snapshot()
    assert snap == []
    ev = q.get_nowait()
    assert ev["event"] == "gateway_approval.responded"
    assert ev["choice"] == "once"
    assert ev["run_id"] == "r1"


def test_handle_frame_run_terminal_clears_with_reason() -> None:
    w = GatewayApprovalWatcher()
    w._handle_frame(
        run_id="r1", kind="outreach", campaign_id="CID-1",
        event_name="approval.request",
        data_str=json.dumps({"command": "x", "description": "y"}),
    )
    q = w.subscribe()
    for inner, expected_reason in [
        ("run.completed", "run_completed"),
        ("run.failed", "run_failed"),
        ("run.cancelled", "run_cancelled"),
    ]:
        # Re-seed since each terminal clears.
        w._open(
            run_id="r1", kind="outreach", campaign_id="CID-1",
            payload={"command": "x", "description": "y"},
        )
        # Drain the queue from the re-seed.
        while not q.empty():
            q.get_nowait()
        terminal = w._handle_frame(
            run_id="r1", kind="outreach", campaign_id="CID-1",
            event_name=inner, data_str=json.dumps({}),
        )
        assert terminal is True, f"{inner} should signal terminal"
        ev = q.get_nowait()
        assert ev["event"] == "gateway_approval.cleared"
        assert ev["reason"] == expected_reason


def test_handle_frame_falls_back_to_payload_event_field() -> None:
    """When the SSE header is missing (older gateway builds), the parser
    must recover the inner event name from ``payload["event"]``.
    """
    w = GatewayApprovalWatcher()
    terminal = w._handle_frame(
        run_id="r1", kind="reply", campaign_id="CID-2",
        event_name="message",
        data_str=json.dumps({
            "event": "approval.request",
            "command": "rm -rf /",
            "description": "danger",
        }),
    )
    assert terminal is False
    snap, _ = w.snapshot()
    assert snap and snap[0]["run_id"] == "r1"


def test_responded_for_unknown_run_is_noop() -> None:
    w = GatewayApprovalWatcher()
    q = w.subscribe()
    # No pending entry → close is a noop, no broadcast.
    w._close("r-unknown", reason="responded", choice="deny")
    assert q.empty()


def test_snapshot_seq_advances_monotonically() -> None:
    w = GatewayApprovalWatcher()
    for i in range(3):
        w._handle_frame(
            run_id=f"r{i}", kind="outreach", campaign_id=f"C{i}",
            event_name="approval.request",
            data_str=json.dumps({"command": "x", "description": "y"}),
        )
    _, seq = w.snapshot()
    assert seq == 3


# ---------------------------------------------------------------------------
# End-to-end: scan + SSE consumption
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """Replacement for httpx.AsyncClient that hands back a canned SSE
    stream for any /v1/runs/{id}/events call. Real httpx behaviours we
    don't care about (TLS, connection pool) are ignored.
    """

    def __init__(self, *, frames_by_run: dict[str, list[str]] | None = None,
                 status_by_run: dict[str, int] | None = None,
                 run_status_by_run: dict[str, dict[str, Any]] | None = None,
                 default_status: int = 200, **_kw) -> None:
        self._frames = frames_by_run or {}
        self._statuses = status_by_run or {}
        self._run_statuses = run_status_by_run or {}
        self._default_status = default_status
        self.calls: list[str] = []

    async def get(self, url: str, headers: dict | None = None, timeout: float | None = None):
        self.calls.append(url)
        try:
            rid = url.split("/v1/runs/")[1].split("/")[0]
        except IndexError:
            rid = ""
        resp = _FakeJsonResponse(
            status_code=200,
            payload=self._run_statuses.get(rid, {"status": "running"}),
        )
        return resp

    def stream(self, method: str, url: str, headers: dict | None = None, **_kw):
        self.calls.append(url)
        # Pull run id out of the path tail.
        # url shape: ...gateway_base/v1/runs/{rid}/events
        try:
            rid = url.split("/v1/runs/")[1].split("/")[0]
        except IndexError:
            rid = ""
        status = self._statuses.get(rid, self._default_status)
        lines = self._frames.get(rid, [])
        return _FakeStreamResponse(lines, status_code=status)

    async def aclose(self) -> None:
        return None


class _FakeJsonResponse:
    def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _sse_lines(*frames: tuple[str, dict[str, Any]]) -> list[str]:
    """Build a list of raw SSE protocol lines from (event, payload)
    pairs.  ``httpx.aiter_lines`` strips trailing newlines and yields one
    item per line, so an empty string between frames marks the boundary.
    """
    out: list[str] = []
    for name, payload in frames:
        out.append(f"event: {name}")
        out.append(f"data: {json.dumps(payload)}")
        out.append("")  # frame terminator
    return out


async def _drain_queue(q: asyncio.Queue, *, count: int, timeout: float = 2.0):
    results = []
    for _ in range(count):
        ev = await asyncio.wait_for(q.get(), timeout=timeout)
        results.append(ev)
    return results


@pytest.mark.asyncio
async def test_end_to_end_request_then_responded(tmp_path: Path) -> None:
    db = _seed_db(tmp_path, runs=[("r1", "CID-1", "outreach")])
    w = GatewayApprovalWatcher()
    w._settings = _make_settings(db_path=db)
    w._client = _FakeAsyncClient(frames_by_run={
        "r1": _sse_lines(
            ("approval.request", {
                "command": "rm -rf /tmp/x",
                "description": "recursive delete",
                "pattern_key": "rm-rf",
                "pattern_keys": ["rm-rf"],
                "choices": ["once", "session", "always", "deny"],
                "timestamp": 1700000000,
            }),
            ("approval.responded", {"choice": "deny", "resolved": 1}),
            ("run.completed", {"status": "completed"}),
        ),
    })
    q = w.subscribe()
    # Drive one scan; this spawns the per-run task.
    await w._scan_once()
    assert "r1" in w._subs
    # Drain three broadcasts: request → responded → cleared(run_completed)
    # Note: responded already cleared the entry, so run.completed is a noop;
    # we expect exactly 2 events.
    events = await _drain_queue(q, count=2)
    assert events[0]["event"] == "gateway_approval.request"
    assert events[1]["event"] == "gateway_approval.responded"
    # Wait for the task to wrap up.
    await asyncio.wait_for(w._subs["r1"], timeout=2.0)
    snap, _ = w.snapshot()
    assert snap == []


@pytest.mark.asyncio
async def test_run_failed_clears_pending_with_reason(tmp_path: Path) -> None:
    db = _seed_db(tmp_path, runs=[("r2", "CID-2", "reply")])
    w = GatewayApprovalWatcher()
    w._settings = _make_settings(db_path=db)
    w._client = _FakeAsyncClient(frames_by_run={
        "r2": _sse_lines(
            ("approval.request", {"command": "x", "description": "y"}),
            ("run.failed", {"error": "boom"}),
        ),
    })
    q = w.subscribe()
    await w._scan_once()
    events = await _drain_queue(q, count=2)
    assert events[0]["event"] == "gateway_approval.request"
    assert events[1]["event"] == "gateway_approval.cleared"
    assert events[1]["reason"] == "run_failed"
    await asyncio.wait_for(w._subs["r2"], timeout=2.0)


@pytest.mark.asyncio
async def test_gateway_404_clears_with_evicted(tmp_path: Path) -> None:
    db = _seed_db(tmp_path, runs=[("r3", "CID-3", "outreach")])
    w = GatewayApprovalWatcher()
    w._settings = _make_settings(db_path=db)
    w._client = _FakeAsyncClient(frames_by_run={"r3": []},
                                  status_by_run={"r3": 404})
    # Seed a pending entry as if a prior watcher iteration captured it
    # before the gateway evicted the run.
    w._open(run_id="r3", kind="outreach", campaign_id="CID-3",
            payload={"command": "x", "description": "y"})
    q = w.subscribe()
    await w._scan_once()
    # First broadcast was the seed (request) → ignore. Then the 404 path
    # broadcasts cleared.
    ev = await asyncio.wait_for(q.get(), timeout=2.0)
    assert ev["event"] == "gateway_approval.cleared"
    assert ev["reason"] == "evicted"
    await asyncio.wait_for(w._subs["r3"], timeout=2.0)


@pytest.mark.asyncio
async def test_scan_skips_already_subscribed_runs(tmp_path: Path) -> None:
    """Two scan passes for the same run_id must not spawn a second task —
    we'd otherwise double-consume the upstream SSE.
    """
    db = _seed_db(tmp_path, runs=[("r4", "CID-4", "draft")])
    w = GatewayApprovalWatcher()
    w._settings = _make_settings(db_path=db)
    # A frame list with no terminator — the fake will exhaust the iterator
    # and then aiter_lines stops; the task will sleep waiting to reconnect.
    w._client = _FakeAsyncClient(frames_by_run={"r4": []})
    await w._scan_once()
    task = w._subs["r4"]
    await w._scan_once()
    # Same task object — no replacement.
    assert w._subs["r4"] is task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_db_read_filters_ended_and_old_runs(tmp_path: Path) -> None:
    """The active-run query must drop ended_at-set rows and pre-cutoff
    rows so the watcher doesn't keep retrying long-evicted runs forever.
    """
    db = tmp_path / "scan.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            env TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            session_id TEXT,
            dedup_key TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT
        )"""
    )
    now = _utcnow()
    long_ago = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=3)
    ).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO product_campaign_runs "
        "(campaign_id,env,run_id,kind,started_at,ended_at) VALUES (?,?,?,?,?,?)",
        [
            ("C", "TEST", "live-run", "outreach", now, None),
            ("C", "TEST", "ended-run", "outreach", now, now),
            ("C", "TEST", "stale-run", "outreach", long_ago, None),
        ],
    )
    conn.commit()
    conn.close()
    from app.gateway_approval_watcher import _read_active_runs
    rows = _read_active_runs(db)
    run_ids = {r["run_id"] for r in rows}
    assert run_ids == {"live-run"}


@pytest.mark.asyncio
async def test_poll_surfaces_waiting_for_approval_when_sse_missed(tmp_path: Path) -> None:
    """GET /v1/runs/{id} fallback when status is waiting_for_approval."""
    db = _seed_db(tmp_path, runs=[("r-poll", "CID-P", "redraft-outreach")])
    w = GatewayApprovalWatcher()
    w._settings = _make_settings(db_path=db)
    w._client = _FakeAsyncClient(
        frames_by_run={
            "r-poll": _sse_lines(
                ("approval.request", {
                    "command": "python3 bridge.py campaigns redraft",
                    "description": "redraft outreach",
                    "pattern_key": "bridge",
                    "pattern_keys": ["bridge"],
                    "timestamp": 1700000001,
                }),
            ),
        },
        run_status_by_run={
            "r-poll": {"status": "waiting_for_approval"},
        },
    )
    q = w.subscribe()
    await w._scan_once()
    ev = await asyncio.wait_for(q.get(), timeout=2.0)
    assert ev["event"] == "gateway_approval.request"
    assert "redraft" in ev["command"]
    snap, _ = w.snapshot()
    assert len(snap) == 1
    assert snap[0]["run_id"] == "r-poll"
