#!/usr/bin/env python3
"""POVISON catalog CLI for the povison-seo agent.

Wraps ``povison_catalog.py`` so the agent can search products, look up a
product's main image / specs, and get topic-fit recommendations without
spawning the Bridge HTTP server. Mirrors ``search-stock-images.py``.

Subcommands:
  search    keyword search → candidates (name, url, image, tags)
  lookup    PDP URL/path → name, url, image, specs, dimensions (Detail API)
  scrape    PDP URL → JSON-LD Product fallback (when Detail API fails)
  enrich    PDP URL → best-effort {image, name} (Detail API then scrape)
  recommend topic + sections → scored products[] ready for articleState

Examples:
  python3 scripts/povison-catalog.py search -q "low profile tv stand" -n 8
  python3 scripts/povison-catalog.py lookup --url "https://www.povison.com/..."
  python3 scripts/povison-catalog.py recommend --topic '{"primary_keyword":"low profile tv stand","secondary_keywords":["modern tv stand"]}' --sections /tmp/sections.json

Env:
  SEO_STUDIO_DIR — path to playground/seo-studio (imports povison_catalog)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_catalog_module():
    """Import povison_catalog from SEO Studio playground."""
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
        if d and (d / "povison_catalog.py").exists():
            sys.path.insert(0, str(d))
            import povison_catalog  # type: ignore

            return povison_catalog
    return None


def _emit(result: dict, output: str | None) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="POVISON catalog CLI (search / lookup / scrape / enrich / recommend)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Keyword search → candidates")
    p_search.add_argument("-q", "--keyword", required=True)
    p_search.add_argument("-n", "--limit", type=int, default=15)

    p_lookup = sub.add_parser("lookup", help="PDP URL/path → detail (Detail API)")
    p_lookup.add_argument("--url", required=True)
    p_lookup.add_argument("--variant", default=None)

    p_scrape = sub.add_parser("scrape", help="PDP URL → JSON-LD Product fallback")
    p_scrape.add_argument("--url", required=True)

    p_enrich = sub.add_parser("enrich", help="PDP URL → best-effort {image, name}")
    p_enrich.add_argument("--url", required=True)

    p_rec = sub.add_parser("recommend", help="Topic + sections → scored products[]")
    p_rec.add_argument("--topic", required=True, help='JSON string: {"primary_keyword":..., "secondary_keywords":[...], "category_keywords":[...]}')
    p_rec.add_argument("--sections", help="Path to sections JSON file (articleState.sections)")
    p_rec.add_argument("--limit", type=int, default=2)

    for p in (p_search, p_lookup, p_scrape, p_enrich, p_rec):
        p.add_argument("-o", "--output", help="Write JSON result to this path")

    args = ap.parse_args()
    mod = _load_catalog_module()
    if mod is None:
        print(json.dumps({"ok": False, "error": "povison_catalog module not found; set SEO_STUDIO_DIR"}, ensure_ascii=False))
        return 2

    if args.cmd == "search":
        return _emit(mod.search_products(args.keyword, page=1, page_size=args.limit), args.output)

    if args.cmd == "lookup":
        return _emit(mod.lookup_detail(args.url, variant=args.variant), args.output)

    if args.cmd == "scrape":
        return _emit(mod.scrape_pdp(args.url), args.output)

    if args.cmd == "enrich":
        detail = mod.lookup_detail(args.url)
        if detail.get("ok") and detail.get("image"):
            return _emit({"ok": True, "image": detail["image"], "name": detail.get("name", "")}, args.output)
        sc = mod.scrape_pdp(args.url)
        if sc.get("ok") and sc.get("image"):
            return _emit({"ok": True, "image": sc["image"], "name": sc.get("name", "")}, args.output)
        return _emit({"ok": False, "error": detail.get("error") or "enrich failed"}, args.output)

    if args.cmd == "recommend":
        try:
            topic = json.loads(args.topic)
        except Exception as e:
            return _emit({"ok": False, "error": f"invalid --topic JSON: {e}"}, args.output)
        sections = []
        if args.sections:
            try:
                sections = json.loads(Path(args.sections).read_text(encoding="utf-8"))
            except Exception as e:
                return _emit({"ok": False, "error": f"invalid --sections file: {e}"}, args.output)
        return _emit(mod.recommend_placements(topic, sections, limit=args.limit), args.output)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
