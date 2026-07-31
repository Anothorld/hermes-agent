#!/usr/bin/env python3
"""POVISON reviews CLI for the povison-seo agent.

Wraps ``povison_reviews.py`` so the agent (and operators) can fetch real
APPROVED buyer reviews from the magento2 DB without spawning the Bridge HTTP
server. Read-only; the only deterministic surface for review access (§4).

Subcommands:
  fetch     SPU → list of APPROVED reviews (best-rated first)
  summary   SPU → pre-aggregated {reviewsCount, ratingSummary, rating}

Examples:
  python3 scripts/povison-reviews.py fetch --spu 123 --limit 5 --min-rating 4
  python3 scripts/povison-reviews.py summary --spu 123

Env:
  MAGENTO_DB_HOST / MAGENTO_DB_PORT / MAGENTO_DB_USER / MAGENTO_DB_PASS / MAGENTO_DB_NAME
  SEO_STUDIO_DIR  — path to playground/seo-studio (imports povison_reviews)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_reviews_module():
    """Import povison_reviews from SEO Studio playground (mirrors catalog CLI)."""
    candidates = []
    env_dir = os.environ.get("SEO_STUDIO_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[1],  # scripts/ → seo-studio/
            Path.home() / "agent_prj/hermes-agent/playground/seo-studio",
            Path("/Users/arnold/agent_prj/hermes-agent/playground/seo-studio"),
        ]
    )
    for d in candidates:
        if d and (d / "povison_reviews.py").exists():
            sys.path.insert(0, str(d))
            import povison_reviews  # type: ignore

            return povison_reviews
    return None


def _cmd_fetch(args, mod) -> int:
    out = mod.fetch_reviews(spu=args.spu, limit=args.limit, min_rating=args.min_rating)
    print(json.dumps({"ok": bool(out), "spu": args.spu, "count": len(out), "reviews": out}, ensure_ascii=False, indent=2))
    return 0


def _cmd_summary(args, mod) -> int:
    out = mod.fetch_summary(spu=args.spu)
    print(json.dumps({"ok": out["reviewsCount"] > 0, "spu": args.spu, **out}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Read-only POVISON review fetcher (magento2 DB).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="List APPROVED reviews for a SPU.")
    p_fetch.add_argument("--spu", required=True, help="magento product_id (SPU)")
    p_fetch.add_argument("--limit", type=int, default=5, help="max reviews (1-50, default 5)")
    p_fetch.add_argument("--min-rating", type=int, default=0, help="min star rating 1-5 (default 0=no filter)")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_sum = sub.add_parser("summary", help="Aggregate count + rating for a SPU.")
    p_sum.add_argument("--spu", required=True, help="magento product_id (SPU)")
    p_sum.set_defaults(func=_cmd_summary)
    return ap


def main() -> int:
    # Parse args first so --help works even when env is not configured.
    args = build_parser().parse_args()
    mod = _load_reviews_module()
    if mod is None:
        print(json.dumps({"ok": False, "error": "povison_reviews.py not found (set SEO_STUDIO_DIR)"}))
        return 2
    if not mod.is_configured():
        print(json.dumps({"ok": False, "error": "MAGENTO_DB_* env not configured (need HOST + PASS)"}))
        # Not a crash — return 0 so callers can parse the JSON; a 2 would break pipelines.
        return 0
    return args.func(args, mod)


if __name__ == "__main__":
    raise SystemExit(main())
