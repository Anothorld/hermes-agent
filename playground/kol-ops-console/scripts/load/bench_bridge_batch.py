#!/usr/bin/env python3
"""Benchmark bridge-heavy Console paths (lanes, batch facts, approve)."""

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


def _timed(client: httpx.Client, method: str, url: str, **kwargs: Any) -> tuple[float, int]:
    t0 = time.perf_counter()
    resp = client.request(method, url, **kwargs)
    return time.perf_counter() - t0, resp.status_code


def main() -> int:
    p = argparse.ArgumentParser(description="Bench bridge batch endpoints")
    p.add_argument("--base", default="http://127.0.0.1:8765")
    p.add_argument("--token", required=True)
    p.add_argument("--env", default="TEST")
    p.add_argument("--campaign-id", default="")
    p.add_argument("--rounds", type=int, default=20)
    args = p.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"}
    latencies: list[float] = []
    statuses: list[int] = []

    with httpx.Client(timeout=60.0, headers=headers) as client:
        for _ in range(args.rounds):
            elapsed, status = _timed(
                client, "GET", f"{args.base}/products/summary", params={"env": args.env},
            )
            latencies.append(elapsed)
            statuses.append(status)
            if args.campaign_id:
                elapsed, status = _timed(
                    client,
                    "GET",
                    f"{args.base}/kols/lanes",
                    params={"env": args.env, "campaign_id": args.campaign_id},
                )
                latencies.append(elapsed)
                statuses.append(status)

    report = {
        "rounds": args.rounds,
        "requests": len(latencies),
        "status_histogram": {str(s): statuses.count(s) for s in sorted(set(statuses))},
        "latency_sec": {
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0,
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
