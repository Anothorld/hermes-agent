"""No-progress watchdog detection (POVISON 686 backstop).

``APIServerAdapter._stuck_run_ids`` must flag only genuinely-stalled
in-flight runs: status exactly ``running``, a live task, and an agent whose
last activity (events OR tool heartbeats) is older than the ceiling. It must
never flag ``waiting_for_approval`` (legitimately blocked on the operator),
terminal runs, or runs that are still making progress.
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway.platforms.api_server import APIServerAdapter


class _FakeTask:
    def __init__(self, done: bool = False):
        self._done = done

    def done(self) -> bool:
        return self._done


def _fake(run_statuses, agents, tasks):
    return SimpleNamespace(
        _run_statuses=run_statuses,
        _active_run_agents=agents,
        _active_run_tasks=tasks,
    )


def _detect(fake, now, ceiling):
    return APIServerAdapter._stuck_run_ids(fake, now, ceiling)


def test_flags_running_run_with_stale_activity():
    now = 10_000.0
    fake = _fake(
        {"r1": {"status": "running", "updated_at": now - 5000}},
        {"r1": SimpleNamespace(_last_activity_ts=now - 4000)},
        {"r1": _FakeTask(done=False)},
    )
    stuck = _detect(fake, now, ceiling=1800)
    assert [rid for rid, _ in stuck] == ["r1"]
    assert stuck[0][1] >= 1800


def test_skips_fresh_activity():
    now = 10_000.0
    fake = _fake(
        {"r1": {"status": "running", "updated_at": now - 5000}},
        {"r1": SimpleNamespace(_last_activity_ts=now - 30)},  # heartbeat 30s ago
        {"r1": _FakeTask(done=False)},
    )
    assert _detect(fake, now, ceiling=1800) == []


def test_skips_waiting_for_approval():
    now = 10_000.0
    fake = _fake(
        {"r1": {"status": "waiting_for_approval", "updated_at": now - 9000}},
        {"r1": SimpleNamespace(_last_activity_ts=now - 9000)},
        {"r1": _FakeTask(done=False)},
    )
    assert _detect(fake, now, ceiling=1800) == []


def test_skips_terminal_and_queued():
    now = 10_000.0
    fake = _fake(
        {
            "done": {"status": "completed", "updated_at": now - 9000},
            "q": {"status": "queued", "updated_at": now - 9000},
        },
        {"done": SimpleNamespace(_last_activity_ts=now - 9000),
         "q": SimpleNamespace(_last_activity_ts=now - 9000)},
        {"done": _FakeTask(done=True), "q": _FakeTask(done=False)},
    )
    assert _detect(fake, now, ceiling=1800) == []


def test_skips_when_task_done_or_agent_missing():
    now = 10_000.0
    fake = _fake(
        {"r1": {"status": "running", "updated_at": now - 9000},
         "r2": {"status": "running", "updated_at": now - 9000}},
        {"r1": SimpleNamespace(_last_activity_ts=now - 9000)},  # r2 has no agent
        {"r1": _FakeTask(done=True), "r2": _FakeTask(done=False)},
    )
    assert _detect(fake, now, ceiling=1800) == []


def test_falls_back_to_updated_at_before_first_heartbeat():
    now = 10_000.0
    # Agent has not ticked activity yet (_last_activity_ts=0); use updated_at.
    fake = _fake(
        {"r1": {"status": "running", "updated_at": now - 5000}},
        {"r1": SimpleNamespace(_last_activity_ts=0)},
        {"r1": _FakeTask(done=False)},
    )
    assert [rid for rid, _ in _detect(fake, now, ceiling=1800)] == ["r1"]
    # Just-started run (fresh updated_at, no tick) must not be flagged.
    fake2 = _fake(
        {"r1": {"status": "running", "updated_at": now - 10}},
        {"r1": SimpleNamespace(_last_activity_ts=0)},
        {"r1": _FakeTask(done=False)},
    )
    assert _detect(fake2, now, ceiling=1800) == []
