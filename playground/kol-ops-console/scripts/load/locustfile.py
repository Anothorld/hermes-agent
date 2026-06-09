#!/usr/bin/env python3
"""Locust load wrapper for KOL Ops Console hot paths.

Requires: ``pip install locust httpx``

Usage::

    export KOC_JWT="<bearer token>"
    locust -f scripts/load/locustfile.py --host http://127.0.0.1:8765

Headless smoke::

    locust -f scripts/load/locustfile.py --host http://127.0.0.1:8765 \\
        --headless -u 4 -r 1 -t 60s
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task


class ConsoleOperator(HttpUser):
    """Simulate operator polling + light writes."""

    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        token = os.environ.get("KOC_JWT", "").strip()
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    @task(3)
    def perf_snapshot(self) -> None:
        self.client.get("/admin/perf-snapshot", name="/admin/perf-snapshot")

    @task(5)
    def run_launch_status(self) -> None:
        self.client.get("/campaigns/run-launch-status", name="/campaigns/run-launch-status")

    @task(4)
    def agent_sessions(self) -> None:
        self.client.get("/campaigns/agent-sessions?env=TEST&limit=20", name="/campaigns/agent-sessions")

    @task(2)
    def products_summary(self) -> None:
        self.client.get("/products/summary?env=TEST", name="/products/summary")
