"""Campaign + candidate subcommands for ``kol_bridge_tool``.

Covers everything under ``/campaigns/{id}`` and the discovery pool:
``upsert-campaign``, ``get-campaign``, ``add-candidate``,
``list-candidates``, ``resolve-relationships``, ``select-candidates``,
``route-discovery``, ``get-lanes``.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cal_client import (  # noqa: E402  (after sys.path shim)
    _die,
    add_common_args,
    add_env_arg,
    client_from_args,
    parse_json_arg,
    print_json,
    require_keys,
)


# ----------------------------------------------------------------- handlers
def cmd_upsert_campaign(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.title:
        body.setdefault("label", args.title)
    if args.paid_ceiling is not None:
        body.setdefault("paid_ceiling", args.paid_ceiling)
    if args.contract_required is not None:
        body.setdefault("contract_required", args.contract_required)
    if args.sku_whitelist:
        body.setdefault("sku_whitelist", args.sku_whitelist)
    if args.test_mode_to:
        body.setdefault("test_mode_to", args.test_mode_to)
    print_json(client_from_args(args).request(
        "PUT", f"/campaigns/{args.campaign_id}", body=body,
    ))


def cmd_get_campaign(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/campaigns/{args.campaign_id}",
        params={"env": args.env},
    ))


def cmd_add_candidate(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.primary_handle:
        body.setdefault("primary_handle", args.primary_handle)
    if args.identity_id is not None:
        body.setdefault("identity_id", args.identity_id)
    if args.platform:
        body.setdefault("platform", args.platform)
    if args.source:
        body.setdefault("source", args.source)
    if args.discovery_score is not None:
        body.setdefault("discovery_score", args.discovery_score)
    require_keys(body, "source")
    if "identity_id" not in body and "primary_handle" not in body:
        _die("must_provide_identity_id_or_primary_handle")
    print_json(client_from_args(args).request(
        "POST", f"/campaigns/{args.campaign_id}/candidates", body=body,
    ))


def cmd_list_candidates(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/campaigns/{args.campaign_id}/candidates",
        params={"env": args.env},
    ))


def cmd_resolve_relationships(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "POST",
        f"/campaigns/{args.campaign_id}/candidates/resolve-relationships",
        params={"env": args.env},
    ))


def cmd_select_candidates(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    body.setdefault("env", args.env)
    body.setdefault("selected_by", args.selected_by or "agent")
    require_keys(body, "identity_ids")
    print_json(client_from_args(args).request(
        "POST", f"/campaigns/{args.campaign_id}/candidates/select", body=body,
    ))


def cmd_set_candidate_status(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.identity_ids:
        body.setdefault("identity_ids", args.identity_ids)
    if args.candidate_status:
        body.setdefault("candidate_status", args.candidate_status)
    if args.review_reason:
        body.setdefault("review_reason", args.review_reason)
    require_keys(body, "identity_ids", "candidate_status")
    print_json(client_from_args(args).request(
        "POST", f"/campaigns/{args.campaign_id}/candidates/status", body=body,
    ))


def cmd_route_discovery(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "selected_by": args.selected_by or "agent",
        "operator_note": args.operator_note or "",
    }
    print_json(client_from_args(args).request(
        "POST", f"/campaigns/{args.campaign_id}/candidates/route-discovery",
        body=body,
    ))


def cmd_get_lanes(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/campaigns/{args.campaign_id}/lanes",
        params={"env": args.env},
    ))


# -------------------------------------------------------------- registration
def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "upsert-campaign",
        help=("PUT /campaigns/{id} — create or update a campaign_config. "
              "Use --json for full body or short flags for common fields."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--title", help="Becomes campaign_config.label")
    p.add_argument("--paid-ceiling", type=float)
    p.add_argument("--contract-required", type=lambda v: v.lower() == "true",
                   metavar="true|false")
    p.add_argument("--sku-whitelist", nargs="+",
                   help="One or more SKU codes or product URLs.")
    p.add_argument("--test-mode-to", help="TEST-mode recipient for draft/test outbound mail.")
    p.add_argument("--json", help="Full CampaignConfigUpsertBody as JSON or @path")
    p.set_defaults(func=cmd_upsert_campaign)

    p = sub.add_parser(
        "get-campaign", help="GET /campaigns/{id} — read campaign_config.",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_get_campaign)

    p = sub.add_parser(
        "add-candidate",
        help=("POST /campaigns/{id}/candidates — append one candidate. "
              "Either --identity-id (existing) or --primary-handle (will upsert)."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--identity-id", type=int)
    p.add_argument("--primary-handle", help="instagram handle without leading @")
    p.add_argument("--platform", default=None, help="default 'instagram'")
    p.add_argument("--source", help="e.g. 'discovery:hashtag_search'")
    p.add_argument("--discovery-score", type=float)
    p.add_argument("--json", help="Full CandidateUpsertBody as JSON or @path")
    p.set_defaults(func=cmd_add_candidate)

    p = sub.add_parser("list-candidates",
                       help="GET /campaigns/{id}/candidates — list discovery pool.")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_list_candidates)

    p = sub.add_parser(
        "resolve-relationships",
        help="POST .../candidates/resolve-relationships — re-run resolution pass.",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_resolve_relationships)

    p = sub.add_parser(
        "select-candidates",
        help=("POST .../candidates/select — promote into selected_for_outreach. "
              "JSON body must contain {identity_ids: [..]}; selected_by optional."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--selected-by", default="agent")
    p.add_argument("--json", required=True)
    p.set_defaults(func=cmd_select_candidates)

    p = sub.add_parser(
        "set-candidate-status",
        help="POST .../candidates/status — mark candidates discovered/rejected/etc.",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--identity-ids", type=int, nargs="+")
    p.add_argument("--candidate-status")
    p.add_argument("--review-reason")
    p.add_argument("--json", help="Full CandidateStatusBody as JSON or @path")
    p.set_defaults(func=cmd_set_candidate_status)

    p = sub.add_parser(
        "route-discovery",
        help=("POST .../candidates/route-discovery — deterministic D→O router. "
              "Resolves relationships then promotes new_prospect / repeat_kol "
              "into outreach and opens an escalation for repeat_kol_needs_review."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--selected-by", default="agent")
    p.add_argument("--operator-note", default="",
                   help="One-line summary attached to any opened escalations.")
    p.set_defaults(func=cmd_route_discovery)

    p = sub.add_parser("get-lanes",
                       help="GET /campaigns/{id}/lanes — kanban snapshot per identity.")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--campaign-id", required=True)
    p.set_defaults(func=cmd_get_lanes)
