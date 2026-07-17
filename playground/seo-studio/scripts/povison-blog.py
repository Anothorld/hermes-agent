#!/usr/bin/env python3
"""POVISON blog internal-link CLI for the povison-seo agent.

Wraps ``povison_blog.py`` so the agent can search real povison.com/blog
articles and get topic-fit internal-link recommendations without spawning the
Bridge HTTP server. Internal links MUST come from here (or
``/api/povison-blog/*``) — fabricating blog URLs is forbidden (they 404).

Subcommands:
  search           keyword → ranked blog articles (url, slug, title, score)
  recommend-links  topic + sections → 2-3 links ready for articleState.links
  verify           is a URL a real povison.com/blog/ article?

Examples:
  python3 scripts/povison-blog.py search -q "sofa bed materials" -n 5
  python3 scripts/povison-blog.py verify --url "https://www.povison.com/blog/..."
  python3 scripts/povison-blog.py recommend-links \
    --topic '{"primary_keyword":"sofa bed","secondary_keywords":["sleeper sofa"]}' \
    --sections /tmp/sections.json --limit 3

Env:
  SEO_STUDIO_DIR — path to playground/seo-studio (imports povison_blog)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_blog_module():
    """Import povison_blog from SEO Studio playground."""
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
        if d and (d / "povison_blog.py").exists():
            sys.path.insert(0, str(d))
            import povison_blog  # type: ignore

            return povison_blog
    return None


def _emit(result: dict, output: str | None) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="POVISON blog internal-link CLI (search / recommend-links / verify)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Keyword search → ranked blog articles")
    p_search.add_argument("-q", "--keyword", required=True)
    p_search.add_argument("-n", "--limit", type=int, default=10)
    p_search.add_argument("--refresh", action="store_true", help="Force-refresh sitemap cache")

    p_rec = sub.add_parser("recommend-links", help="Topic + sections → 2-3 internal links")
    p_rec.add_argument("--topic", required=True, help='JSON: {"primary_keyword":..., "secondary_keywords":[...], "category_keywords":[...]}')
    p_rec.add_argument("--sections", help="Path to sections JSON file (articleState.sections)")
    p_rec.add_argument("--existing-urls", help="JSON array of URLs already in articleState.links (to skip)")
    p_rec.add_argument("--limit", type=int, default=3)

    p_ver = sub.add_parser("verify", help="Is a URL a real povison.com/blog/ article?")
    p_ver.add_argument("--url", required=True)

    for p in (p_search, p_rec, p_ver):
        p.add_argument("-o", "--output", help="Write JSON result to this path")

    args = ap.parse_args()
    mod = _load_blog_module()
    if mod is None:
        print(json.dumps({"ok": False, "error": "povison_blog module not found; set SEO_STUDIO_DIR"}, ensure_ascii=False))
        return 2

    if args.cmd == "search":
        if args.refresh:
            mod.fetch_sitemap(force=True)
        return _emit(mod.search_articles(args.keyword, limit=args.limit), args.output)

    if args.cmd == "recommend-links":
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
        existing = []
        if args.existing_urls:
            try:
                existing = json.loads(args.existing_urls)
            except Exception as e:
                return _emit({"ok": False, "error": f"invalid --existing-urls JSON: {e}"}, args.output)
        return _emit(mod.recommend_links(topic, sections, existing_urls=existing, limit=args.limit), args.output)

    if args.cmd == "verify":
        return _emit(mod.verify_url(args.url), args.output)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
