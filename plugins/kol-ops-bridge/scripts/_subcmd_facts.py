"""Facts subcommands for ``kol_bridge_tool``.

Covers ``write-facts``, ``write-facts-multi``, ``get-facts``.
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


# ----------------------------------------------------------------- handlers
def cmd_write_facts(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    body.setdefault("env", args.env)
    require_keys(body, "facts", "namespace")
    if not isinstance(body["facts"], dict):
        _die("json_facts_must_be_object")
    print_json(client_from_args(args).request(
        "POST", f"/facts/{args.identity_id}", body=body,
    ))


def cmd_write_facts_multi(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    body.setdefault("env", args.env)
    require_keys(body, "namespaces")
    if not isinstance(body["namespaces"], dict):
        _die("json_namespaces_must_be_object")
    print_json(client_from_args(args).request(
        "POST", f"/facts/{args.identity_id}/multi", body=body,
    ))


def cmd_get_facts(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request(
        "GET", f"/facts/{args.identity_id}",
        params={
            "env": args.env,
            "campaign_id": args.campaign_id,
        },
    ))


# -------------------------------------------------------------- registration
def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "write-facts",
        help=("POST /facts/{identity_id} — write facts for an identity. "
              "JSON body must contain {facts: {<namespace>.<key>: <val>, ...}, "
              "namespace, source, optional campaign_id}."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--json", required=True, help="JSON body or @path")
    p.set_defaults(func=cmd_write_facts)

    p = sub.add_parser(
        "write-facts-multi",
        help=("POST /facts/{identity_id}/multi — write facts across N namespaces "
              "in one transaction. JSON body must contain "
              "{namespaces: {<ns>: {<ns>.<key>: <val>, ...}}, source, "
              "optional campaign_id}. Replaces N back-to-back write-facts calls."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--json", required=True, help="JSON body or @path")
    p.set_defaults(func=cmd_write_facts_multi)

    p = sub.add_parser(
        "get-facts",
        help="GET /facts/{identity_id} — read latest facts (optionally scoped by campaign).",
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", default=None)
    p.set_defaults(func=cmd_get_facts)
