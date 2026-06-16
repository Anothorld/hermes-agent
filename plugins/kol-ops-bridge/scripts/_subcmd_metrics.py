"""KOL discovery statistics subcommands for ``kol_bridge_tool``.

Wraps gate-metrics Bridge endpoints under ``/kol-registry/*`` so agents and
SKILL procedures can read discovery pool counts, trends, and the paginated
registry table without hand-rolled curl or direct CAL SQL.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cal_client import (  # noqa: E402
    add_common_args,
    add_env_arg,
    client_from_args,
    print_json,
)

_DISCOVERY_SECTIONS = (
    "summary",
    "trend",
    "funnel",
    "funnel_trend",
    "registry",
)
_TREND_BUCKETS = ("day", "week", "month", "year")


def _discovery_summary_params(args: argparse.Namespace) -> dict[str, Any]:
    return {"env": args.env}


def _discovery_trend_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"env": args.env, "bucket": args.bucket}
    if args.periods is not None:
        params["periods"] = args.periods
    return params


def _discovery_funnel_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {"env": args.env}
    if args.days is not None:
        params["days"] = args.days
    return params


def _kol_registry_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {
        "env": args.env,
        "source": args.source,
        "sort": args.sort,
        "order": args.order,
        "limit": args.limit,
        "offset": args.offset,
    }
    if args.q:
        params["q"] = args.q
    return params


def cmd_get_discovery_summary(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/kol-registry/summary", params=_discovery_summary_params(args),
    ))


def cmd_get_discovery_summary_trend(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET",
        "/kol-registry/summary/trend",
        params=_discovery_trend_params(args),
    ))


def cmd_get_discovery_funnel(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/kol-registry/funnel", params=_discovery_funnel_params(args),
    ))


def cmd_get_discovery_funnel_trend(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET",
        "/kol-registry/funnel/trend",
        params=_discovery_trend_params(args),
    ))


def cmd_list_kol_registry(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/kol-registry", params=_kol_registry_params(args),
    ))


def cmd_get_discovery_stats(args: argparse.Namespace) -> None:
    """Fetch one or more discovery metric sections in a single JSON envelope."""
    client = client_from_args(args)
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    unknown = [s for s in sections if s not in _DISCOVERY_SECTIONS]
    if unknown:
        raise SystemExit(
            f"invalid --sections {unknown!r}; "
            f"choose from: {', '.join(_DISCOVERY_SECTIONS)}",
        )
    if not sections:
        sections = ["summary"]

    out: dict[str, Any] = {"env": args.env, "sections": {}}
    if "summary" in sections:
        out["sections"]["summary"] = client.request(
            "GET", "/kol-registry/summary", params=_discovery_summary_params(args),
        )
    if "trend" in sections:
        out["sections"]["trend"] = client.request(
            "GET",
            "/kol-registry/summary/trend",
            params=_discovery_trend_params(args),
        )
    if "funnel" in sections:
        out["sections"]["funnel"] = client.request(
            "GET", "/kol-registry/funnel", params=_discovery_funnel_params(args),
        )
    if "funnel_trend" in sections:
        out["sections"]["funnel_trend"] = client.request(
            "GET",
            "/kol-registry/funnel/trend",
            params=_discovery_trend_params(args),
        )
    if "registry" in sections:
        out["sections"]["registry"] = client.request(
            "GET", "/kol-registry", params=_kol_registry_params(args),
        )
    print_json(out)


def _add_trend_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--bucket",
        choices=_TREND_BUCKETS,
        default="week",
        help="Trend granularity (default: week).",
    )
    p.add_argument(
        "--periods",
        type=int,
        default=None,
        help="Number of buckets to return (Bridge default varies by bucket).",
    )


def _add_registry_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--q", default=None, help="Search handle or email substring.")
    p.add_argument(
        "--source",
        choices=("all", "legacy", "discovery"),
        default="all",
        help="Registry source filter (default: all).",
    )
    p.add_argument(
        "--sort",
        choices=("ingested_at", "first_discovered_at", "created_at"),
        default="ingested_at",
    )
    p.add_argument("--order", choices=("asc", "desc"), default="desc")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)


def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "get-discovery-summary",
        help=(
            "GET /kol-registry/summary — full discovery pool counts "
            "(passed/pending/rejected, initial outreach reply rates)."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    p.set_defaults(func=cmd_get_discovery_summary)

    p = sub.add_parser(
        "get-discovery-summary-trend",
        help=(
            "GET /kol-registry/summary/trend — cumulative discovery counts "
            "by day/week/month/year."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    _add_trend_args(p)
    p.set_defaults(func=cmd_get_discovery_summary_trend)

    p = sub.add_parser(
        "get-discovery-funnel",
        help=(
            "GET /kol-registry/funnel — legacy mature-cohort funnel "
            "(adoption + reply rates with prior-touch exclusion)."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Rolling window in days (omit for all-time cohort).",
    )
    p.set_defaults(func=cmd_get_discovery_funnel)

    p = sub.add_parser(
        "get-discovery-funnel-trend",
        help="GET /kol-registry/funnel/trend — time-bucketed legacy funnel rates.",
    )
    add_common_args(p)
    add_env_arg(p)
    _add_trend_args(p)
    p.set_defaults(func=cmd_get_discovery_funnel_trend)

    p = sub.add_parser(
        "list-kol-registry",
        help=(
            "GET /kol-registry — paginated Agent-discovered KOL table "
            "(metrics page 红人列表)."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    _add_registry_args(p)
    p.set_defaults(func=cmd_list_kol_registry)

    p = sub.add_parser(
        "get-discovery-stats",
        help=(
            "Fetch one or more KOL discovery metric sections in one JSON response "
            "(summary, trend, funnel, funnel_trend, registry)."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument(
        "--sections",
        default="summary",
        help=(
            "Comma-separated sections: summary, trend, funnel, funnel_trend, "
            "registry (default: summary)."
        ),
    )
    _add_trend_args(p)
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Rolling window for funnel section (omit for all-time).",
    )
    _add_registry_args(p)
    p.set_defaults(func=cmd_get_discovery_stats)
