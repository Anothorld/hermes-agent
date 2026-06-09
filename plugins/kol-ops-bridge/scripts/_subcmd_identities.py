"""Identity + event + timeline subcommands for ``kol_bridge_tool``.

Covers ``upsert-identity``, ``get-identity``, ``get-relationship``,
``list-relationships``, ``get-reusable-facts``, ``get-goals``,
``get-dispatch-context``, ``get-email-conversation``, ``get-timeline``,
``archive-identity``, ``list-events``, ``write-event``.
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
        "GET",
        f"/identities/{args.identity_id}",
        params={"env": args.env},
    ))


def cmd_list_outreach_cooldown_handles(args: argparse.Namespace) -> None:
    data = client_from_args(args).request(
        "GET",
        "/outreach-touch/cooldown-handles",
        params={"env": args.env, "limit": args.limit},
    )
    if args.plain:
        for row in data.get("items", []):
            handle = row.get("handle")
            if handle:
                print(handle)
        return
    print_json(data)


def cmd_list_discovery_skip_handles(args: argparse.Namespace) -> None:
    data = client_from_args(args).request(
        "GET",
        "/discovery-skip-handles",
        params={"env": args.env, "limit": args.limit},
    )
    if args.plain:
        for row in data.get("items", []):
            handle = row.get("handle")
            if handle:
                print(handle)
        return
    print_json(data)


def cmd_batch_outreach_touch(args: argparse.Namespace) -> None:
    ids = [int(x) for x in args.identity_ids.split(",") if x.strip().isdigit()]
    print_json(client_from_args(args).request(
        "GET",
        "/identities/outreach-touch",
        params={"env": args.env, "identity_ids": ",".join(str(i) for i in ids)},
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


def cmd_transfer_campaign(args: argparse.Namespace) -> None:
    body = {
        "from_campaign_id": args.from_campaign_id,
        "to_campaign_id": args.to_campaign_id,
        "env": args.env,
        "source_stage": "shortlist",
        "reason": args.reason or "",
        "operator_note": args.operator_note or "",
    }
    print_json(client_from_args(args).request(
        "POST",
        f"/identities/{args.identity_id}/transfer-campaign",
        body=body,
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


def _operator_headers(args: argparse.Namespace) -> dict[str, str] | None:
    if getattr(args, "operator_user_id", None) is None:
        return None
    return {"X-KOC-Operator-User-Id": str(args.operator_user_id)}


def cmd_get_email_conversation(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/email-conversation",
        params={"campaign_id": args.campaign_id, "env": args.env},
        extra_headers=_operator_headers(args),
    ))


def cmd_get_reply_chase_hint(args: argparse.Namespace) -> None:
    params = {
        "campaign_id": args.campaign_id,
        "message_id": args.message_id,
        "env": args.env,
    }
    if args.thread_id:
        params["thread_id"] = args.thread_id
    print_json(client_from_args(args).request(
        "GET", f"/identities/{args.identity_id}/reply-chase-hint",
        params=params,
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


def _write_event_missing_fields(
    *,
    identity_id: object,
    event_type: object,
    actor: object,
) -> list[str]:
    missing: list[str] = []
    if identity_id is None:
        missing.append("identity_id (pass --identity-id <id>)")
    if not event_type:
        missing.append("event_type (pass --event-type <name>)")
    if not actor:
        missing.append("actor (pass --actor <name>)")
    return missing


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
    missing = _write_event_missing_fields(
        identity_id=body.get("identity_id"),
        event_type=body.get("event_type"),
        actor=body.get("actor"),
    )
    if missing:
        import json
        import sys

        payload = {
            "error": "invalid_cli_args",
            "hint": (
                "write-event needs identity_id, event_type, and actor. "
                "Example: write-event --identity-id 689 --campaign-id CID "
                "--env LIVE --event-type shortlist_approval_received "
                "--actor owner@console.app --json @/tmp/event.json"
            ),
            "missing": missing,
            "canonical_cli": "plugins/kol-ops-bridge/scripts/kol_bridge_tool.py",
        }
        _line = json.dumps(payload, ensure_ascii=False) + "\n"
        sys.stdout.write(_line)
        sys.stdout.flush()
        sys.stderr.write(_line)
        raise SystemExit(2)
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

    p = sub.add_parser(
        "list-outreach-cooldown-handles",
        help=("GET /outreach-touch/cooldown-handles — handles blocked from "
              "discovery for 14 days after last outreach send."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--limit", type=int, default=5000)
    p.add_argument("--plain", action="store_true",
                   help="Print one handle per line (for brief exclusion sets).")
    p.set_defaults(func=cmd_list_outreach_cooldown_handles)

    p = sub.add_parser(
        "list-discovery-skip-handles",
        help=("GET /discovery-skip-handles — handles blocked from discovery "
              "(历史合作/已合作/主动叫停/竞品归档结论)."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--limit", type=int, default=10_000)
    p.add_argument("--plain", action="store_true",
                   help="Print one handle per line (for brief exclusion sets).")
    p.set_defaults(func=cmd_list_discovery_skip_handles)

    p = sub.add_parser(
        "batch-outreach-touch",
        help="GET /identities/outreach-touch — prior outreach timestamps for IDs.",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-ids", required=True,
                   help="Comma-separated identity_id values.")
    p.set_defaults(func=cmd_batch_outreach_touch)

    p = sub.add_parser("get-relationship",
                       help="GET /identities/{id}/relationship — identity-level relationship row.")
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--identity-id", type=int, required=True)
    p.set_defaults(func=cmd_get_relationship)

    p = sub.add_parser(
        "list-relationships",
        help=("GET /relationships — list KOL relationship rows (archived KOLs + "
              "outcome filters). Prefer list-discovery-skip-handles for the "
              "discovery exclusion set."),
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

    p = sub.add_parser(
        "transfer-campaign",
        help=(
            "POST .../transfer-campaign — move KOL between campaign shortlists "
            "(pre-approval only)."
        ),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--from-campaign-id", required=True)
    p.add_argument("--to-campaign-id", required=True)
    p.add_argument("--reason", default="", help="Operator reason (optional).")
    p.add_argument("--operator-note", default="", help="Extra note (optional).")
    p.set_defaults(func=cmd_transfer_campaign)

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

    p = sub.add_parser(
        "get-email-conversation",
        description=(
            "GET .../email-conversation — Gmail sent/received thread for one KOL "
            "(console communication panel). Requires --campaign-id. Pass "
            "--operator-user-id when mailbox binding is per operator."
        ),
        help="GET .../email-conversation — Gmail thread (not drafts).",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", required=True)
    p.add_argument(
        "--operator-user-id",
        type=int,
        default=None,
        help="X-KOC-Operator-User-Id header (mailbox-scoped history).",
    )
    p.set_defaults(func=cmd_get_email_conversation)

    p = sub.add_parser(
        "get-reply-chase-hint",
        help=("GET .../reply-chase-hint — follow-up supersede policy for one inbound."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--message-id", required=True)
    p.add_argument("--thread-id", default=None)
    p.set_defaults(func=cmd_get_reply_chase_hint)

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
              "Required: --identity-id, --event-type, --actor. Prefer "
              "`--json @/tmp/event.json` over inline JSON in the shell."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int)
    p.add_argument("--event-type",
                   help="e.g. shortlist_approval_received, kol_initial_outreach_draft_ready")
    p.add_argument("--actor", help="e.g. 'owner@console.app', 'skill:kol-cold-outreach'")
    p.add_argument("--campaign-id")
    p.add_argument("--json", help="EventWriteBody JSON or @/tmp/event.json (recommended)")
    p.set_defaults(func=cmd_write_event)
