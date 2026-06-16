"""Contract render + preview CLI wrappers."""

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
)


def cmd_render_contract(args: argparse.Namespace) -> None:
    payload = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST",
        "/contracts/render",
        body=payload,
    ))


def cmd_get_contract_preview(args: argparse.Namespace) -> None:
    params = {
        "campaign_id": args.campaign_id,
        "env": args.env,
    }
    if args.attachment_path:
        params["attachment_path"] = args.attachment_path
    print_json(client_from_args(args).request(
        "GET",
        f"/identities/{args.identity_id}/contract-preview",
        params=params,
    ))


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "render-contract",
        help=("POST /contracts/render — render POVISON agreement docx with "
              "formal filename under ~/.hermes/kol-ops-bridge/contracts/."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument(
        "--json",
        required=True,
        help='{"identity_id", "campaign_id", "env", "fields": {...}} JSON or @path',
    )
    p.set_defaults(func=cmd_render_contract)

    p = sub.add_parser(
        "get-contract-preview",
        help=("GET .../contract-preview — HTML preview + formal display name."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--identity-id", type=int, required=True)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--attachment-path", default=None)
    p.set_defaults(func=cmd_get_contract_preview)
