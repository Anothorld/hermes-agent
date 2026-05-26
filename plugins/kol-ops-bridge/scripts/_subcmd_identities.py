"""Identity + event + timeline subcommands for ``kol_bridge_tool``.

Covers ``upsert-identity``, ``get-identity``, ``get-relationship``,
``list-relationships``, ``get-reusable-facts``, ``get-goals``,
``get-dispatch-context``, ``get-timeline``, ``archive-identity``,
``list-events``, ``write-event``.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cal_client import (  # noqa: E402
    add_common_args,
    add_env_arg,
    client_from_args,
    parse_json_arg,
    print_json,
    require_keys,
)


# ----------------------------------------------------------------- handlers
def cmd_upsert_identity(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.primary_handle:
        body.setdefault("primary_handle", args.primary_handle)
    if args.platform:
        body.setdefault("platform", args.platform)
    if args.primary_email:
        body.setdefault("primary_email", args.primary_email)
    if args.display_name:
        body.setdefault("display_name", args.display_name)
    require_keys(body, "primary_handle")
    print_json(client_from_args(args).request(
        "POST", "/identities", body=body,
    ))


def cmd_get_identity(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}",
    ))


def cmd_get_relationship(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/relationship",
    ))


def cmd_list_relationships(args: argparse.Namespace) -> None:
    params: dict[str, object] = {
        "env": args.env,
        "limit": args.limit,
        "offset": args.offset,
    }
    if args.last_outcome:
        params["last_outcome"] = args.last_outcome
    if args.platform:
        params["platform"] = args.platform
    if args.q:
        params["q"] = args.q
    print_json(client_from_args(args).request(
        "GET", "/relationships", params=params,
    ))


def cmd_get_reusable_facts(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/relationship/reusable-facts",
    ))


def cmd_get_goals(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/goals",
        params={"campaign_id": args.campaign_id, "env": args.env},
    ))


def cmd_get_dispatch_context(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/dispatch-context",
        params={"campaign_id": args.campaign_id, "env": args.env},
    ))


def cmd_get_timeline(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/timeline",
        params={
            "env": args.env,
            "campaign_id": args.campaign_id,
            "limit": args.limit,
        },
    ))


def cmd_archive_identity(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    if args.outcome:
        body.setdefault("outcome", args.outcome)
    if args.campaign_id:
        body.setdefault("campaign_id", args.campaign_id)
    if args.decided_by:
        body.setdefault("decided_by", args.decided_by)
    require_keys(body, "campaign_id", "outcome")
    print_json(client_from_args(args).request(
        "POST", f"/identities/{args.identity_id}/archive", body=body,
    ))


def cmd_list_events(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/events/recent",
        params={
            "env": args.env,
            "campaign_id": args.campaign_id,
            "since_id": args.since_id,
            "limit": args.limit,
        },
    ))


def cmd_write_event(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.identity_id is not None:
        body.setdefault("identity_id", args.identity_id)
    if args.event_type:
        body.setdefault("event_type", args.event_type)
    if args.actor:
        body.setdefault("actor", args.actor)
    if args.campaign_id:
        body.setdefault("campaign_id", args.campaign_id)
    require_keys(body, "identity_id", "event_type", "actor")
    print_json(client_from_args(args).request(
        "POST", "/events", body=body,
    ))


# -------------------------------------------------------------- registration
def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "upsert-identity",
        help=("POST /identities — create or update a KOL identity. "
              "Required: --primary-handle (or full --json body)."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--primary-handle")
    p.add_argument("--platform", default=None, help="default 'instagram'")
    p.add_argument("--primary-email")
    p.add_argument("--display-name")
    p.add_argument("--json", help="Full IdentityUpsertBody as JSON or @path")
    p.set_defaults(func=cmd_upsert_identity)

    p = sub.add_parser("get-identity",
                       help="GET /identities/{id} — read the identity row.")
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--identity-id", type=int, required=True)
    p.set_defaults(func=cmd_get_identity)

    p = sub.add_parser("get-relationship",
                       help="GET /identities/{id}/relationship — identity-level relationship row.")
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--identity-id", type=int, required=True)
    p.set_defaults(func=cmd_get_relationship)

    p = sub.add_parser(
        "list-relationships",
        help=("GET /relationships — list KOL relationship rows (archived KOLs + "
              "outcome filters). Used by the discovery skills to fetch the "
              "operator-maintained do-not-contact set (e.g. --last-outcome competitor)."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--last-outcome", default=None,
                   help="Filter by exact last_outcome value (e.g. 'competitor').")
    p.add_argument("--platform", default=None)
    p.add_argument("--q", default=None, help="Fuzzy match on handle/display_name/email.")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_list_relationships)

    p = sub.add_parser("get-reusable-facts",
                       help="GET .../relationship/reusable-facts — facts reusable across campaigns.")
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--identity-id", type=int, required=True)
    p.set_defaults(func=cmd_get_reusable_facts)

    p = sub.add_parser("get-goals",
                       help="GET /identities/{id}/goals — 10 goal states for (identity, campaign).")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_get_goals)

    p = sub.add_parser(
        "get-dispatch-context",
        help=("GET .../dispatch-context — goal_state + lanes + relationship + "
              "reusable_facts in one call (replaces 4)."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_get_dispatch_context)

    p = sub.add_parser("get-timeline",
                       help="GET /identities/{id}/timeline — reverse-chrono event log for one KOL.")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_get_timeline)

    p = sub.add_parser(
        "archive-identity",
        help=("POST /identities/{id}/archive — close a collab and persist "
              "post-mortem facts (outcome / preferred_skus / quality scores)."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id")
    p.add_argument("--outcome", help="e.g. 'shipped', 'cancelled', 'no_show'")
    p.add_argument("--decided-by", help="defaults to 'skill:archival-writer'")
    p.add_argument("--json", help="Full ArchiveBody as JSON or @path")
    p.set_defaults(func=cmd_archive_identity)

    p = sub.add_parser(
        "list-events",
        help=("GET /events/recent — paginated event log across identities. "
              "Use --since-id for incremental pulls (pollers)."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", default=None)
    p.add_argument("--since-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_list_events)

    p = sub.add_parser(
        "write-event",
        help=("POST /events — append one row to kol_conversation_events. "
              "Required: --identity-id, --event-type, --actor."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int)
    p.add_argument("--event-type",
                   help="e.g. inbound_reply, outbound_draft, escalation_opened")
    p.add_argument("--actor", help="e.g. 'agent', 'operator:alice', 'gmail:reply-poller'")
    p.add_argument("--campaign-id")
    p.add_argument("--json", help="Full EventWriteBody as JSON or @path "
                                  "(use for --payload nested data)")
    p.set_defaults(func=cmd_write_event)
