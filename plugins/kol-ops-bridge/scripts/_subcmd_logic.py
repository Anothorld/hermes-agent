"""Deterministic-logic subcommands for ``kol_bridge_tool``.

Thin HTTP wrappers over the Bridge's toolized skill-step endpoints. These
replace model-generated reasoning that used to live inside the KOL skills:

- ``compute-compensation-offer`` → ``POST /logic/compute-compensation-offer``
  (pricing engine; was the ``kol-pricing-strategist`` skill).
- ``validate-campaign-config``   → ``POST /logic/validate-campaign-config``
  (campaign intake safety-field validators).
- ``select-next-skill``          → ``POST /logic/select-next-skill``
  (dispatcher Steps 4-5 lane routing).
- ``select-draftable-plan``      → ``POST /logic/select-draftable-plan``
  (multi-goal fragment dispatch plan).
- ``match-escalation-rules``     → ``POST /logic/match-escalation-rules``
  (classifier rule matching).
- ``sanitize-classifier-facts``  → ``POST /logic/sanitize-classifier-facts``
  (preview Step 3 committed-key rewrites before write-facts-multi).
- ``persist-reply-draft``        → ``POST /reply-drafts/persist``
  (dispatcher Step 5.5 envelope enrichment + atomic event/approval write).
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
def cmd_compute_compensation_offer(args: argparse.Namespace) -> None:
    payload = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/compute-compensation-offer", body={"payload": payload},
    ))


def cmd_validate_campaign_config(args: argparse.Namespace) -> None:
    candidate = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/validate-campaign-config",
        body={
            "campaign_id": args.campaign_id,
            "candidate": candidate,
            "confirmed_high_budget": bool(args.confirmed_high_budget),
        },
    ))


def cmd_select_next_skill(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/select-next-skill", body=body,
    ))


def cmd_select_draftable_plan(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/select-draftable-plan", body=body,
    ))


def cmd_match_escalation_rules(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/match-escalation-rules", body=body,
    ))


def cmd_sanitize_classifier_facts(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    print_json(client_from_args(args).request(
        "POST", "/logic/sanitize-classifier-facts", body=body,
    ))


def cmd_persist_reply_draft(args: argparse.Namespace) -> None:
    body = parse_json_arg(args.json)
    body.setdefault("env", args.env)
    require_keys(
        body, "identity_id", "campaign_id", "source_message_id",
        "primary_lane", "primary_goal", "child_skill",
        "child_envelope", "latest_email",
    )
    print_json(client_from_args(args).request(
        "POST", "/reply-drafts/persist", body=body,
    ))


# -------------------------------------------------------------- registration
def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "compute-compensation-offer",
        help=("POST /logic/compute-compensation-offer — deterministic pricing "
              "recommendation (number/bounds/human-gate). --json is the "
              "pricing situation payload."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--json", required=True, help="Pricing payload JSON or @path.")
    p.set_defaults(func=cmd_compute_compensation_offer)

    p = sub.add_parser(
        "validate-campaign-config",
        help=("POST /logic/validate-campaign-config — validate an extracted "
              "campaign_config candidate (missing/invalid/cap_review)."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--campaign-id", required=True)
    p.add_argument("--confirmed-high-budget", action="store_true",
                   help="Operator already approved an over-ceiling budget.")
    p.add_argument("--json", required=True, help="Candidate config JSON or @path.")
    p.set_defaults(func=cmd_validate_campaign_config)

    p = sub.add_parser(
        "select-next-skill",
        help=("POST /logic/select-next-skill — pick the primary lane/skill and "
              "side-topics from a goal_state + classifier signals snapshot."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--json", required=True,
                   help="{goals, facts, signals, meta} JSON or @path.")
    p.set_defaults(func=cmd_select_next_skill)

    p = sub.add_parser(
        "select-draftable-plan",
        help=("POST /logic/select-draftable-plan — list all draftable goals "
              "for multi-fragment dispatch (same payload as select-next-skill)."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--json", required=True,
                   help="{goals, facts, signals, meta, lane_filter?} JSON or @path.")
    p.set_defaults(func=cmd_select_draftable_plan)

    p = sub.add_parser(
        "match-escalation-rules",
        help=("POST /logic/match-escalation-rules — deterministically match "
              "signals against escalation_rules; returns escalation_hint. "
              "Omit 'parsed' in the body to use the active policy doc."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--json", required=True,
                   help="{signals, parsed?} JSON or @path.")
    p.set_defaults(func=cmd_match_escalation_rules)

    p = sub.add_parser(
        "sanitize-classifier-facts",
        help=("POST /logic/sanitize-classifier-facts — preview classifier "
              "committed-key rewrites from {namespaces, signals}."),
    )
    add_common_args(p)
    add_env_arg(p, required=False)
    p.add_argument("--json", required=True,
                   help="{namespaces, signals} JSON or @path.")
    p.set_defaults(func=cmd_sanitize_classifier_facts)

    p = sub.add_parser(
        "persist-reply-draft",
        help=("POST /reply-drafts/persist — enrich a child reply envelope "
              "(to/Re:subject/thread_id) then write the draft event + "
              "approval.reply_draft fact atomically."),
    )
    add_common_args(p)
    add_env_arg(p)
    p.add_argument("--json", required=True,
                   help="PersistReplyDraftBody JSON or @path.")
    p.set_defaults(func=cmd_persist_reply_draft)
