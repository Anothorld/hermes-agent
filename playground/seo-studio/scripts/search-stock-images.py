#!/usr/bin/env python3
"""Search Unsplash/Pexels for SEO blog body-image candidates.

Used by the povison-seo agent during section generation (P0+P1 image flow).
Prefer this over browser scraping — returns a constrained candidate pool.

Examples:
  python3 scripts/search-stock-images.py -q "modern living room sofa" -n 5
  python3 scripts/search-stock-images.py -q "dining table small apartment" --source pexels -o /tmp/stock.json

Env:
  UNSPLASH_ACCESS_KEY / PEXELS_API_KEY — API keys
  SEO_STUDIO_DIR — path to playground/seo-studio (imports stock_images.py)
  SEO_BRIDGE_BASE — optional fallback HTTP Bridge (default http://127.0.0.1:8766)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_stock_module():
    """Import stock_images from SEO Studio playground."""
    candidates = []
    env_dir = os.environ.get("SEO_STUDIO_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    # Common local layout: agent_prj/hermes-agent/playground/seo-studio
    here = Path(__file__).resolve()
    candidates.extend(
        [
            Path.home() / "agent_prj/hermes-agent/playground/seo-studio",
            here.parents[4] / "agent_prj/hermes-agent/playground/seo-studio"
            if len(here.parents) > 4
            else Path("/"),
            Path("/Users/arnold/agent_prj/hermes-agent/playground/seo-studio"),
        ]
    )
    for d in candidates:
        if d and (d / "stock_images.py").exists():
            sys.path.insert(0, str(d))
            import stock_images  # type: ignore

            return stock_images
    return None


def _via_bridge(query: str, source: str, per_page: int) -> dict:
    import urllib.request

    base = os.environ.get("SEO_BRIDGE_BASE", "http://127.0.0.1:8766").rstrip("/")
    payload = json.dumps({"query": query, "source": source, "per_page": per_page}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/stock-images/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Search Unsplash/Pexels stock images for blog sections")
    ap.add_argument("-q", "--query", required=True, help="Concrete English search phrase")
    ap.add_argument("-n", "--per-page", type=int, default=5, help="Candidates (1-10)")
    ap.add_argument(
        "--source",
        default="auto",
        choices=["auto", "unsplash", "pexels"],
        help="Stock source (default: auto)",
    )
    ap.add_argument("-o", "--output", help="Write JSON result to this path")
    ap.add_argument(
        "--bridge",
        action="store_true",
        help="Force Bridge HTTP API instead of local stock_images import",
    )
    args = ap.parse_args()

    result = None
    err = None
    if not args.bridge:
        mod = _load_stock_module()
        if mod is not None:
            try:
                result = mod.search_stock_images(
                    args.query, source=args.source, per_page=args.per_page
                )
            except Exception as e:  # noqa: BLE001
                err = str(e)

    if result is None:
        try:
            result = _via_bridge(args.query, args.source, args.per_page)
        except Exception as e:  # noqa: BLE001
            result = {
                "ok": False,
                "query": args.query,
                "candidates": [],
                "error": f"local={err!r}; bridge={e}",
            }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
