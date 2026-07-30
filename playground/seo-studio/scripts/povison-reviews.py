#!/usr/bin/env python3
"""POVISON reviews CLI for the povison-seo agent.

Wraps ``povison_reviews.py`` so the agent can fetch real APPROVED customer
reviews from the magento2 database when writing Editorial Picks cards.
Mirrors ``povison-catalog.py``.

Subcommands:
  fetch     by SPU → top APPROVED reviews (nickname/date/rating/detail)
  summary   by SPU → pre-aggregated reviewsCount + ratingSummary
  resolve   variant id or PDP URL → SPU id

Examples:
  python3 scripts/povison-reviews.py fetch --spu 1234 --limit 5 --min-rating 4
  python3 scripts/povison-reviews.py summary --spu 1234
  python3 scripts/povison-reviews.py resolve --variant 5678

Env (loaded by server._load_dotenv or the operator's shell):
  SEO_STUDIO_REVIEWS_MYSQL_HOST
  SEO_STUDIO_REVIEWS_MYSQL_USER
  SEO_STUDIO_REVIEWS_MYSQL_PASS   (or SEO_STUDIO_REVIEWS_MYSQL_PASSWORD)
  SEO_STUDIO_REVIEWS_MYSQL_DB     (default: magento2)
  SEO_STUDIO_REVIEWS_MYSQL_PORT   (default: 3306)
  SEO_STUDIO_REVIEWS_MYSQL_TIMEOUT (default: 8)
  SEO_STUDIO_DIR — path to playground/seo-studio (imports povison_reviews)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_reviews_module():
    """Import povison_reviews from SEO Studio playground."""
    candidates = []
    env_dir = os.environ.get("SEO_STUDIO_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
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
        if d and (d / "povison_reviews.py").exists():
            sys.path.insert(0, str(d))
            import povison_reviews  # type: ignore
            return povison_reviews
    return None


def _emit(result: dict, output: str | None) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="POVISON reviews CLI (fetch / summary / resolve)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="By SPU → top APPROVED reviews")
    p_fetch.add_argument("--spu", required=True, help="product SPU id (review_product.product_id)")
    p_fetch.add_argument("--limit", type=int, default=5)
    p_fetch.add_argument("--min-rating", type=int, default=0, help="0-5; 4 = only 4+ star reviews")

    p_sum = sub.add_parser("summary", help="By SPU → aggregated reviewsCount + ratingSummary")
    p_sum.add_argument("--spu", required=True)

    p_res = sub.add_parser("resolve", help="Variant id or PDP URL → SPU id")
    p_res.add_argument("--variant", default=None, help="variant/sku id")
    p_res.add_argument("--url", default=None, help="PDP URL (parses ?variant=)")

    for p in (p_fetch, p_sum, p_res):
        p.add_argument("-o", "--output", help="Write JSON result to this path")

    args = ap.parse_args()
    mod = _load_reviews_module()
    if mod is None:
        print(json.dumps({"ok": False, "error": "povison_reviews module not found; set SEO_STUDIO_DIR"}, ensure_ascii=False))
        return 2

    if args.cmd == "fetch":
        return _emit(mod.fetch_reviews(args.spu, limit=args.limit, min_rating=args.min_rating), args.output)

    if args.cmd == "summary":
        return _emit(mod.summary(args.spu), args.output)

    if args.cmd == "resolve":
        spu = mod.resolve_spu(variant=args.variant, product_url=args.url)
        return _emit({"ok": bool(spu), "spu": spu}, args.output)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
