"""Regression: state_lock serializes concurrent callers."""

from __future__ import annotations

import threading
import time

from kol_ops_bridge_pkg.inbound_reply.state import state_lock


def test_state_lock_serializes_threads(bridge_pkg):
    order: list[str] = []

    def worker(tag: str) -> None:
        with state_lock():
            order.append(f"{tag}-start")
            time.sleep(0.05)
            order.append(f"{tag}-end")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert order == ["a-start", "a-end", "b-start", "b-end"]
