"""Governance subcommands: escalations, approvals, policies.

Covers:
- ``list-escalations``, ``open-escalation``, ``resolve-escalation``
- ``list-approvals``, ``approve``, ``reject``
- ``get-policy``, ``set-policy``, ``list-policy-history``,
  ``get-parsed-escalation-rules``
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cal_client import (  # noqa: E402
    _die,
    add_common_args,
    add_env_arg,
    client_from_args,
    parse_json_arg,
    print_json,
    require_keys,
)


# -------------------------------------------------------------- escalations
def cmd_list_escalations(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/escalations",
        params={"env": args.env, "state": args.state},
    ))


def cmd_open_escalation(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    body.setdefault("env", args.env)
    require_keys(body, "reason")
    print_json(client_from_args(args).request(
        "POST", "/escalations", body=body,
    ))


def cmd_resolve_escalation(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    require_keys(body, "decision", "decided_by")
    body.setdefault("final_state", "resolved")
    if body["final_state"] not in {"resolved", "re_escalated", "aborted", "answered"}:
        _die("invalid_final_state", value=body["final_state"])
    print_json(client_from_args(args).request(
        "PATCH", f"/escalations/{args.escalation_id}", body=body,
    ))


# --------------------------------------------------------------- approvals
def cmd_list_approvals(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/approvals",
        params={"env": args.env, "status": args.status},
    ))


def _approval_decision(args: argparse.Namespace, *, kind: str) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    body.setdefault("env", args.env)
    if args.identity_id is not None:
        body.setdefault("identity_id", args.identity_id)
    if args.campaign_id:
        body.setdefault("campaign_id", args.campaign_id)
    if args.decided_by:
        body.setdefault("decided_by", args.decided_by)
    if args.note:
        body.setdefault("note", args.note)
    require_keys(body, "identity_id", "decided_by")
    print_json(client_from_args(args).request(
        "POST", f"/approvals/{args.fact_path}/{kind}", body=body,
    ))


def cmd_approve(args: argparse.Namespace) -> None:
    _approval_decision(args, kind="approve")


def cmd_reject(args: argparse.Namespace) -> None:
    _approval_decision(args, kind="reject")


# ---------------------------------------------------------------- policies
def cmd_get_policy(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/policies/{args.scope}",
        params={"owner_user_id": args.owner_user_id},
    ))


def cmd_set_policy(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json) if args.json else {}
    if args.content_md_file and not body.get("content_md"):
        try:
            with open(args.content_md_file, "rb") as fh:
                body["content_md"] = fh.read().decode("utf-8")
        except OSError as exc:
            _die("content_md_file_read_failed",
                 path=args.content_md_file, detail=str(exc))
    if args.updated_by:
        body.setdefault("updated_by", args.updated_by)
    if args.title:
        body.setdefault("title", args.title)
    if args.owner_user_id is not None:
        body.setdefault("owner_user_id", args.owner_user_id)
    require_keys(body, "content_md", "updated_by")
    print_json(client_from_args(args).request(
        "PUT", f"/policies/{args.scope}", body=body,
    ))


def cmd_list_policy_history(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/policies/{args.scope}/history",
        params={"owner_user_id": args.owner_user_id, "limit": args.limit},
    ))


def cmd_get_parsed_escalation_rules(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", "/policies/escalation_rules/parsed",
    ))


# -------------------------------------------------------------- registration
def register(sub: "argparse._SubParsersAction") -> None:
    # ---- escalations
    p = sub.add_parser("list-escalations",
                       help="GET /escalations — list escalations (optionally by --state).")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--state", default=None,
                   help="awaiting_answer | resolved | aborted | ...")
    p.set_defaults(func=cmd_list_escalations)

    p = sub.add_parser(
        "open-escalation",
        help=("POST /escalations — open one. JSON body must contain {reason} "
              "+ optional {identity_id, campaign_id, goal, severity, "
              "question_to_operator, parent_escalation_id, resume_context}."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--json", required=True, help="JSON body or @path")
    p.set_defaults(func=cmd_open_escalation)

    p = sub.add_parser(
        "resolve-escalation",
        help=("PATCH /escalations/{id} — resolve. JSON body must contain "
              "{decision, decided_by} + optional {operator_answer, "
              "operator_facts, final_state}. final_state defaults to 'resolved'."),
    )
    add_common_args(p)
    p.add_argument("--escalation-id", type=int, required=True)
    p.add_argument("--json", required=True, help="JSON body or @path")
    p.set_defaults(func=cmd_resolve_escalation)

    # ---- approvals
    p = sub.add_parser("list-approvals",
                       help="GET /approvals — pending approval.* facts.")
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--status", default="pending", choices=("pending", "all"))
    p.set_defaults(func=cmd_list_approvals)

    for action, helptxt in (
        ("approve", "POST /approvals/{fact_path}/approve — operator approves."),
        ("reject", "POST /approvals/{fact_path}/reject — operator rejects."),
    ):
        p = sub.add_parser(action, help=helptxt)
        add_common_args(p)
        add_env_arg(p)
        p.add_argument("--fact-path", required=True,
                       help="Must start with 'approval.' (e.g. 'approval.shortlist').")
        p.add_argument("--identity-id", type=int)
        p.add_argument("--campaign-id")
        p.add_argument("--decided-by", help="e.g. 'web:alice@x', 'agent'")
        p.add_argument("--note")
        p.add_argument("--json", help="Full ApprovalDecisionBody as JSON or @path "
                                      "(use for --extra-facts nested data)")
        p.set_defaults(func=cmd_approve if action == "approve" else cmd_reject)

    # ---- policies
    p = sub.add_parser("get-policy",
                       help="GET /policies/{scope} — read latest policy doc.")
    add_common_args(p)
    p.add_argument("--scope", required=True,
                   choices=("company_style", "user_style", "escalation_rules"))
    p.add_argument("--owner-user-id", type=int, default=None,
                   help="Required only for scope=user_style.")
    p.set_defaults(func=cmd_get_policy)

    p = sub.add_parser(
        "set-policy",
        help=("PUT /policies/{scope} — bump a new policy version. "
              "Provide --content-md-file or full --json body."),
    )
    add_common_args(p)
    p.add_argument("--scope", required=True,
                   choices=("company_style", "user_style", "escalation_rules"))
    p.add_argument("--content-md-file", help="Path to markdown file (UTF-8).")
    p.add_argument("--updated-by", help="e.g. 'web:alice@x', 'skill:campaign-intake'")
    p.add_argument("--title")
    p.add_argument("--owner-user-id", type=int, default=None,
                   help="Required only for scope=user_style.")
    p.add_argument("--json", help="Full PolicyPutBody as JSON or @path")
    p.set_defaults(func=cmd_set_policy)

    p = sub.add_parser("list-policy-history",
                       help="GET /policies/{scope}/history — previous versions (newest first).")
    add_common_args(p)
    p.add_argument("--scope", required=True,
                   choices=("company_style", "user_style", "escalation_rules"))
    p.add_argument("--owner-user-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list_policy_history)

    p = sub.add_parser(
        "get-parsed-escalation-rules",
        help=("GET /policies/escalation_rules/parsed — markdown rules as a "
              "structured {top, rules, version} dict for the classifier skill."),
    )
    add_common_args(p)
    p.set_defaults(func=cmd_get_parsed_escalation_rules)
