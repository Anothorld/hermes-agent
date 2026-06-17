#!/usr/bin/env python3
"""Deterministic CLI for cs-ops-bridge (HTTP only, no direct SQLite)."""

from __future__ import annotations

import argparse
import json
import sys
import os

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPTS_DIR)

from _cal_client import (  # noqa: E402
    add_common_args,
    add_env_arg,
    client_from_args,
    print_json,
)


def _cmd_health(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request("GET", "/health"))


def _cmd_get_dispatch_context(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "GET",
            f"/sessions/{args.session_id}/dispatch-context",
            query={"env": args.env},
        )
    )


def _cmd_classify_intent(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "POST",
            "/logic/classify-intent",
            body={"subject": args.subject, "body": args.body, "metadata": json.loads(args.metadata or "{}")},
        )
    )


def _cmd_write_event(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "POST",
            "/events",
            body={
                "quickcep_session_id": args.session_id,
                "event_type": args.event_type,
                "payload": json.loads(args.json or "{}"),
                "env": args.env,
            },
        )
    )


def _cmd_write_facts(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "POST",
            "/facts",
            body={
                "quickcep_session_id": args.session_id,
                "namespaces": json.loads(args.json),
                "env": args.env,
            },
        )
    )


def _cmd_update_session_status(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "POST",
            "/sessions/status",
            body={
                "quickcep_session_id": args.session_id,
                "status": args.status,
                "env": args.env,
            },
        )
    )


def _cmd_open_escalation(args: argparse.Namespace) -> None:
    print_json(
        client_from_args(args).request(
            "POST",
            "/escalations",
            body={
                "quickcep_session_id": args.session_id,
                "reason": args.reason,
                "urgency": args.urgency,
                "question_to_operator": args.question,
                "feishu_chat_id": args.feishu_chat_id,
                "feishu_thread_id": args.feishu_thread_id,
                "feishu_message_id": args.feishu_message_id,
                "resume_context": json.loads(args.resume_context or "{}"),
                "env": args.env,
            },
        )
    )


def _cmd_apply_handoff(args: argparse.Namespace) -> None:
    classify: dict = {}
    if args.classify_json:
        classify = json.loads(args.classify_json)
    print_json(
        client_from_args(args).request(
            "POST",
            f"/sessions/{args.session_id}/handoff",
            body={
                "phase": args.phase,
                "env": args.env,
                "customer_need": args.customer_need or "",
                "actions_taken": args.actions_taken or "",
                "follow_up": args.follow_up or "",
                "operator_hint": args.operator_hint or "",
                "error": args.error or "",
                "urgency": args.urgency or "medium",
                "feishu_thread_id": args.feishu_thread_id,
                "classify": classify,
                "chat_session_id": args.chat_session_id,
                "skip_quickcep": args.skip_quickcep,
            },
        )
    )


def _cmd_get_escalation(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request("GET", f"/escalations/{args.escalation_id}"))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cs_bridge_tool")
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("health")
    h.set_defaults(func=_cmd_health)

    g = sub.add_parser("get-dispatch-context")
    add_env_arg(g)
    g.add_argument("--session-id", required=True)
    g.set_defaults(func=_cmd_get_dispatch_context)

    c = sub.add_parser("classify-intent")
    add_env_arg(c)
    c.add_argument("--subject", default="")
    c.add_argument("--body", default="")
    c.add_argument("--metadata", default="{}")
    c.set_defaults(func=_cmd_classify_intent)

    e = sub.add_parser("write-event")
    add_env_arg(e)
    e.add_argument("--session-id", required=True)
    e.add_argument("--event-type", required=True)
    e.add_argument("--json", default="{}")
    e.set_defaults(func=_cmd_write_event)

    f = sub.add_parser("write-facts")
    add_env_arg(f)
    f.add_argument("--session-id", required=True)
    f.add_argument("--json", required=True)
    f.set_defaults(func=_cmd_write_facts)

    s = sub.add_parser("update-session-status")
    add_env_arg(s)
    s.add_argument("--session-id", required=True)
    s.add_argument("--status", required=True)
    s.set_defaults(func=_cmd_update_session_status)

    o = sub.add_parser("open-escalation")
    add_env_arg(o)
    o.add_argument("--session-id", required=True)
    o.add_argument("--reason", required=True)
    o.add_argument("--urgency", default="medium")
    o.add_argument("--question", default=None)
    o.add_argument("--feishu-chat-id", default=None)
    o.add_argument("--feishu-thread-id", default=None)
    o.add_argument("--feishu-message-id", default=None)
    o.add_argument("--resume-context", default="{}")
    o.set_defaults(func=_cmd_open_escalation)

    ge = sub.add_parser("get-escalation")
    add_env_arg(ge)
    ge.add_argument("--escalation-id", type=int, required=True)
    ge.set_defaults(func=_cmd_get_escalation)

    ah = sub.add_parser("apply-handoff")
    add_env_arg(ah)
    ah.add_argument("--session-id", required=True)
    ah.add_argument("--phase", required=True)
    ah.add_argument("--customer-need", default="")
    ah.add_argument("--actions-taken", default="")
    ah.add_argument("--follow-up", default="")
    ah.add_argument("--operator-hint", default="")
    ah.add_argument("--error", default="")
    ah.add_argument("--urgency", default="medium")
    ah.add_argument("--feishu-thread-id", default=None)
    ah.add_argument("--classify-json", default="{}")
    ah.add_argument("--chat-session-id", default=None)
    ah.add_argument("--skip-quickcep", action="store_true")
    ah.set_defaults(func=_cmd_apply_handoff)

    return p


def main() -> None:
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
