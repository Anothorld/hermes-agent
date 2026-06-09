#!/usr/bin/env python3
"""Benchmark gateway run launch throughput (429 rate + queue wait).

Usage::

    python scripts/load/bench_gateway_slots.py \\
        --base http://127.0.0.1:8765 \\
        --token "$KOC_JWT" \\
        --campaign-id POVISON-20260525 \\
        --count 12
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

import httpx


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def main() -> int:
    p = argparse.ArgumentParser(description="Bench gateway launch slots via Console API")
    p.add_argument("--base", default="http://127.0.0.1:8765")
    p.add_argument("--token", required=True)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--env", default="TEST", choices=("TEST", "LIVE"))
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--endpoint", default="discover-email", choices=("discover-email", "start"))
    p.add_argument("--identity-id", type=int, default=1)
    args = p.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"}
    latencies: list[float] = []
    statuses: list[int] = []
    errors: list[str] = []

    with httpx.Client(timeout=120.0, headers=headers) as client:
        for i in range(args.count):
            t0 = time.perf_counter()
            if args.endpoint == "discover-email":
                url = f"{args.base}/kols/{args.identity_id}/discover-email"
                resp = client.post(url, json={"env": args.env, "campaign_id": args.campaign_id})
            else:
                url = f"{args.base}/campaigns/{args.campaign_id}/start"
                resp = client.post(url, json={"env": args.env, "sku": "bench"})
            elapsed = time.perf_counter() - t0
            latencies.append(elapsed)
            statuses.append(resp.status_code)
            if resp.status_code >= 400:
                try:
                    errors.append(json.dumps(resp.json()))
                except Exception:
                    errors.append(resp.text[:200])

    report: dict[str, Any] = {
        "endpoint": args.endpoint,
        "count": args.count,
        "status_histogram": {str(s): statuses.count(s) for s in sorted(set(statuses))},
        "latency_sec": {
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "p99": round(_percentile(latencies, 99), 3),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0,
        },
        "sample_errors": errors[:5],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
