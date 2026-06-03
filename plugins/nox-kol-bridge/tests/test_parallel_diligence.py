"""Concurrency: warmed cache must not double-bill under parallel reads."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal import commands  # noqa: E402


def _run_pack() -> dict:
    return commands.cmd_diligence_pack(
        env="TEST",
        gate="shortlist_confirm",
        monthly_budget=100,
        tz_name="UTC",
        lang="en",
        nox_creator_id="nox_test_creator_001",
        platform=None,
        url=None,
        channel_id=None,
        dimensions=["profile", "audience", "content"],
        include_cooperation=False,
    )


def test_warm_cache_then_parallel_reads_zero_api(nox_home):
    """After one miss, parallel cache hits must not call Nox again."""
    first = _run_pack()
    assert first["cache_hit"] is False
    assert first["api_calls"] == 3

    results: list[dict] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            results.append(_run_pack())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, errors
    assert len(results) == 2
    assert all(r["cache_hit"] for r in results)
    assert sum(int(r.get("api_calls") or 0) for r in results) == 0
