"""``GET /campaigns/agent-sessions`` — cross-campaign session grouping.

The endpoint feeds the global Agent Session Dock. It scans
``product_campaign_runs`` for one env, groups by ``session_id``
(NULL → ``run:{run_id}`` pseudo-key), and returns groups sorted by most
recent activity. Open rows are lightly reconciled against gateway run
terminal state so stale ``ended_at=NULL`` rows do not remain live forever.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import current_user, get_bridge, get_conn, get_gateway  # noqa: E402
from app.routers import campaigns as campaigns_router  # noqa: E402


def _seed_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE product_campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL, env TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            session_id TEXT, dedup_key TEXT,
            started_at TEXT NOT NULL, ended_at TEXT
        )"""
    )
    return conn


def _ts(offset_minutes: int) -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        - _dt.timedelta(minutes=offset_minutes)
    ).isoformat(timespec="seconds")


def _insert(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    run_id: str,
    kind: str,
    session_id: str | None,
    started_at: str,
    ended_at: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO product_campaign_runs
                (campaign_id, env, run_id, kind, session_id,
                 started_at, ended_at)
            VALUES (?,?,?,?,?,?,?)""",
        (campaign_id, env, run_id, kind, session_id, started_at, ended_at),
    )


class _GatewayStub:
    def __init__(self, states: dict[str, dict | None] | None = None) -> None:
        self._states = states or {}

    async def get_run(self, run_id: str) -> dict | None:
        if run_id in self._states:
            return self._states[run_id]
        # Default to running for unspecified IDs so existing grouping tests
        # keep their "open session" expectation unless a test explicitly
        # pins a terminal state.
        return {"status": "running"}


def _build_app(
    conn: sqlite3.Connection, *, gateway: _GatewayStub | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(campaigns_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_bridge] = lambda: object()
    app.dependency_overrides[get_gateway] = lambda: gateway or _GatewayStub()
    app.dependency_overrides[current_user] = lambda: {
        "id": 1, "email": "t@x", "role": "owner", "is_active": 1,
    }
    return TestClient(app)


def test_groups_by_session_id_newest_first() -> None:
    conn = _seed_conn()
    # Session A: two runs in CID-1, the newer is still open.
    _insert(conn, campaign_id="CID-1", env="TEST", run_id="r-a1",
            kind="outreach", session_id="kol-campaign:TEST:CID-1",
            started_at=_ts(60), ended_at=_ts(55))
    _insert(conn, campaign_id="CID-1", env="TEST", run_id="r-a2",
            kind="reply", session_id="kol-campaign:TEST:CID-1",
            started_at=_ts(10), ended_at=None)
    # Session B: one closed run in CID-2.
    _insert(conn, campaign_id="CID-2", env="TEST", run_id="r-b1",
            kind="draft", session_id="kol-campaign-draft:TEST:CID-2:99",
            started_at=_ts(40), ended_at=_ts(38))

    client = _build_app(conn)
    res = client.get("/campaigns/agent-sessions", params={"env": "TEST"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["env"] == "TEST"
    sessions = body["sessions"]
    assert [s["session_id"] for s in sessions] == [
        "kol-campaign:TEST:CID-1",
        "kol-campaign-draft:TEST:CID-2:99",
    ]
    a, b = sessions
    assert a["campaign_id"] == "CID-1"
    assert a["open"] is True
    assert set(a["kinds"]) == {"outreach", "reply"}
    assert len(a["runs"]) == 2
    assert b["campaign_id"] == "CID-2"
    assert b["open"] is False
    assert b["kinds"] == ["draft"]


def test_env_filter_excludes_other_env() -> None:
    conn = _seed_conn()
    _insert(conn, campaign_id="CID-1", env="TEST", run_id="r-t1",
            kind="outreach", session_id="kol-campaign:TEST:CID-1",
            started_at=_ts(20))
    _insert(conn, campaign_id="CID-2", env="LIVE", run_id="r-l1",
            kind="outreach", session_id="kol-campaign:LIVE:CID-2",
            started_at=_ts(5))

    client = _build_app(conn)
    res = client.get("/campaigns/agent-sessions", params={"env": "TEST"})
    assert res.status_code == 200
    sids = [s["session_id"] for s in res.json()["sessions"]]
    assert sids == ["kol-campaign:TEST:CID-1"]


def test_null_session_id_becomes_run_pseudo_session() -> None:
    conn = _seed_conn()
    _insert(conn, campaign_id="CID-3", env="TEST", run_id="r-orphan",
            kind="resume", session_id=None, started_at=_ts(15))

    client = _build_app(conn)
    res = client.get("/campaigns/agent-sessions", params={"env": "TEST"})
    assert res.status_code == 200
    sessions = res.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "run:r-orphan"
    assert sessions[0]["campaign_id"] == "CID-3"
    assert sessions[0]["kinds"] == ["resume"]


def test_agent_sessions_reconciles_terminal_open_runs() -> None:
    conn = _seed_conn()
    _insert(
        conn,
        campaign_id="CID-5",
        env="LIVE",
        run_id="r-failed",
        kind="outreach",
        session_id="kol-campaign:LIVE:CID-5",
        started_at=_ts(3),
        ended_at=None,
    )
    gw = _GatewayStub(states={"r-failed": {"status": "failed"}})
    client = _build_app(conn, gateway=gw)

    res = client.get("/campaigns/agent-sessions", params={"env": "LIVE"})
    assert res.status_code == 200, res.text
    session = res.json()["sessions"][0]
    assert session["session_id"] == "kol-campaign:LIVE:CID-5"
    assert session["open"] is False
    assert session["runs"][0]["ended_at"] is not None

    ended_at = conn.execute(
        "SELECT ended_at FROM product_campaign_runs WHERE run_id='r-failed'"
    ).fetchone()["ended_at"]
    assert ended_at is not None


# ---------------------------------------------------------------------------
# GET /campaigns/agent-sessions/{session_id}/log
# ---------------------------------------------------------------------------


def test_session_log_reads_per_session_file(tmp_path, monkeypatch) -> None:
    # Redirect the sessions dir + state.db to tmp paths so we don't read
    # the real ~/.hermes files. Pointing state.db at a non-existent path
    # forces the JSON-file fallback that this test exercises. Write one
    # session_{sid}.json with messages mixing operator / assistant /
    # tool_call / tool roles.
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_SESSIONS", tmp_path)
    monkeypatch.setattr(
        campaigns_router, "_KOL_ORCHESTRATOR_STATE_DB", tmp_path / "absent.db"
    )
    sid = "kol-campaign:TEST:CID-42"
    payload = {
        "messages": [
            {"role": "user", "content": "kick off the campaign"},
            {"role": "assistant", "content": "looking at the brief…"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "list_candidates",
                            "arguments": "{\"limit\": 5}",
                        }
                    }
                ],
            },
            {"role": "tool", "name": "list_candidates", "content": "{\"ok\": true}"},
            {"role": "assistant", "content": "found five matches"},
        ]
    }
    (tmp_path / f"session_{sid}.json").write_text(__import__("json").dumps(payload))

    conn = _seed_conn()
    client = _build_app(conn)
    res = client.get(
        f"/campaigns/agent-sessions/{sid}/log",
        params={"env": "TEST", "limit": 50},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session_id"] == sid
    kinds = [it["kind"] for it in body["items"]]
    assert kinds == ["user", "assistant", "tool_call", "tool_result", "assistant"]


def test_session_log_missing_file_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_SESSIONS", tmp_path)
    monkeypatch.setattr(
        campaigns_router, "_KOL_ORCHESTRATOR_STATE_DB", tmp_path / "absent.db"
    )
    client = _build_app(_seed_conn())
    res = client.get(
        "/campaigns/agent-sessions/run:r-orphan/log",
        params={"env": "TEST"},
    )
    assert res.status_code == 200
    assert res.json() == {"session_id": "run:r-orphan", "env": "TEST", "items": []}


def _seed_state_db(path, *, session_id: str, rows: list[dict]) -> None:
    """Build a minimal state.db that mirrors hermes's real schema for
    the columns the transcript reader touches. Keeping the schema close
    to production (sessions table + messages table with the same column
    set) catches column-rename regressions without dragging in the full
    hermes migration stack."""
    import sqlite3 as _sql

    conn = _sql.connect(path)
    try:
        conn.execute(
            """CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL
            )"""
        )
        conn.execute(
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, 'gateway', 0)",
            (session_id,),
        )
        for i, row in enumerate(rows):
            conn.execute(
                """INSERT INTO messages
                       (session_id, role, content, tool_calls, tool_name, timestamp)
                   VALUES (?,?,?,?,?,?)""",
                (
                    session_id,
                    row["role"],
                    row.get("content"),
                    row.get("tool_calls"),
                    row.get("tool_name"),
                    float(i),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_session_log_reads_state_db_when_json_missing(tmp_path, monkeypatch) -> None:
    # state.db is hermes's canonical store; the JSON snapshot is opt-in
    # and absent for closed sessions on this profile. The dock must
    # still render the transcript — that's what this test pins down.
    db_path = tmp_path / "state.db"
    sid = "kol-campaign:LIVE:CID-99"
    _seed_state_db(
        db_path,
        session_id=sid,
        rows=[
            {"role": "user", "content": "kick off"},
            {
                "role": "assistant",
                "tool_calls": (
                    "[{\"function\": {\"name\": \"list_candidates\","
                    " \"arguments\": \"{\\\"limit\\\": 3}\"}}]"
                ),
            },
            {"role": "tool", "tool_name": "list_candidates",
             "content": "{\"success\": true, \"count\": 3}"},
            {"role": "assistant", "content": "found three"},
        ],
    )
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_SESSIONS", tmp_path)
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_STATE_DB", db_path)

    client = _build_app(_seed_conn())
    res = client.get(f"/campaigns/agent-sessions/{sid}/log", params={"env": "LIVE"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session_id"] == sid
    kinds = [it["kind"] for it in body["items"]]
    assert kinds == ["user", "tool_call", "tool_result", "assistant"]
    # tool_result error-detection branch should NOT mistake "success: true"
    # for a failure — the row stays a tool_result, not an error.
    assert body["items"][2]["label"] == "list_candidates"


def test_session_log_falls_back_to_json_when_state_db_lacks_session(
    tmp_path, monkeypatch
) -> None:
    # state.db exists but doesn't have THIS session — legacy snapshots
    # written before the profile owned a state.db should still render.
    db_path = tmp_path / "state.db"
    _seed_state_db(db_path, session_id="some-other-session", rows=[])

    sid = "kol-legacy:TEST:CID-legacy"
    payload = {
        "messages": [
            {"role": "user", "content": "legacy ping"},
            {"role": "assistant", "content": "legacy pong"},
        ]
    }
    (tmp_path / f"session_{sid}.json").write_text(
        __import__("json").dumps(payload)
    )

    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_SESSIONS", tmp_path)
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_STATE_DB", db_path)

    client = _build_app(_seed_conn())
    res = client.get(f"/campaigns/agent-sessions/{sid}/log", params={"env": "TEST"})
    assert res.status_code == 200, res.text
    kinds = [it["kind"] for it in res.json()["items"]]
    assert kinds == ["user", "assistant"]


def test_session_log_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(campaigns_router, "_KOL_ORCHESTRATOR_SESSIONS", tmp_path)
    monkeypatch.setattr(
        campaigns_router, "_KOL_ORCHESTRATOR_STATE_DB", tmp_path / "absent.db"
    )
    client = _build_app(_seed_conn())
    # URLs containing literal slashes are blocked by Starlette routing
    # (returns 404 before we run). What our handler must defend against
    # is a session_id WITHOUT slashes that still embeds ``..`` — those
    # do reach the handler and we reject them with 400.
    res = client.get(
        "/campaigns/agent-sessions/..evil../log",
        params={"env": "TEST"},
    )
    assert res.status_code == 400
