#!/usr/bin/env python3
"""Thin CLI for Veedcrawl monthly cache (ops / cron — not discovery main path).

Wraps ``veedcrawl_cache`` + ``veedcrawl_persist.fetch_with_persist`` lookup only.
Agents should use native ``veedcrawl_*`` plugin tools instead.

Usage:
  python plugins/kol-ops-bridge/scripts/veedcrawl_cache_tool.py cache-stats
  python plugins/kol-ops-bridge/scripts/veedcrawl_cache_tool.py cache-lookup --cache-key 'profile:ig:foo:limit=12'
  python plugins/kol-ops-bridge/scripts/veedcrawl_cache_tool.py cache-prune --retain-months 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

import veedcrawl_cache  # noqa: E402


def _print(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_stats(args: argparse.Namespace) -> None:
    _print(veedcrawl_cache.cache_stats(args.month, tz_name=args.tz))


def _cmd_lookup(args: argparse.Namespace) -> None:
    month = args.month or veedcrawl_cache.current_cache_month(args.tz)
    hit = veedcrawl_cache.lookup(month, args.cache_key, tz_name=args.tz)
    if hit is None:
        _print({"ok": False, "cache_month": month, "cache_key": args.cache_key})
        raise SystemExit(1)
    _print({"ok": True, **hit})


def _cmd_prune(args: argparse.Namespace) -> None:
    deleted = veedcrawl_cache.prune_old_months(args.retain_months, tz_name=args.tz)
    _print({
        "ok": True,
        "deleted_entries": deleted,
        "retain_months": args.retain_months,
        "tz": args.tz,
    })


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Veedcrawl monthly cache ops CLI")
    p.add_argument("--tz", default=veedcrawl_cache.DEFAULT_TIMEZONE, help="Cache month TZ")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("cache-stats", help="Show hits/misses/entries for a month")
    s.add_argument("--month", default=None, help="YYYY-MM (default: current)")
    s.set_defaults(func=_cmd_stats)

    l = sub.add_parser("cache-lookup", help="Read one cached response")
    l.add_argument("--cache-key", required=True)
    l.add_argument("--month", default=None)
    l.set_defaults(func=_cmd_lookup)

    pr = sub.add_parser("cache-prune", help="Drop entries/fetch_log/blobs older than retain window")
    pr.add_argument("--retain-months", type=int, default=veedcrawl_cache.DEFAULT_RETAIN_MONTHS)
    pr.set_defaults(func=_cmd_prune)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
