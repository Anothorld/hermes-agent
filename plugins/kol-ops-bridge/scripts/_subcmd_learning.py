"""Learning export, apply, and scheduled job subcommands (Bridge API wrappers)."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cal_client import add_env_arg, client_from_args, print_json  # noqa: E402


def cmd_export_fact_corrections(args: argparse.Namespace) -> None:
    params = {"env": args.env, "limit": args.limit}
    if args.identity_id is not None:
        params["identity_id"] = args.identity_id
    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    print_json(client_from_args(args).request(
        "GET", "/learning/fact-corrections", params=params,
    ))


def cmd_export_negotiation_history(args: argparse.Namespace) -> None:
    params = {"env": args.env, "limit": args.limit}
    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    print_json(client_from_args(args).request(
        "GET", "/learning/negotiation-history", params=params,
    ))


def cmd_export_reject_events(args: argparse.Namespace) -> None:
    params = {"env": args.env, "limit": args.limit}
    if args.identity_id is not None:
        params["identity_id"] = args.identity_id
    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    if args.goal:
        params["goal"] = args.goal
    print_json(client_from_args(args).request(
        "GET", "/learning/reject-events", params=params,
    ))


def cmd_export_edit_events(args: argparse.Namespace) -> None:
    params = {"env": args.env, "limit": args.limit}
    if args.identity_id is not None:
        params["identity_id"] = args.identity_id
    if args.campaign_id:
        params["campaign_id"] = args.campaign_id
    print_json(client_from_args(args).request(
        "GET", "/learning/edit-events", params=params,
    ))


def cmd_apply_reject_policy(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "updated_by": args.updated_by,
        "limit": args.limit,
    }
    print_json(client_from_args(args).request(
        "POST", "/learning/apply-reject-policy", json=body,
    ))


def cmd_apply_edit_policy(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "scope": args.scope,
        "updated_by": args.updated_by,
        "limit": args.limit,
    }
    if args.owner_user_id is not None:
        body["owner_user_id"] = args.owner_user_id
    print_json(client_from_args(args).request(
        "POST", "/learning/apply-edit-policy", json=body,
    ))


def cmd_apply_pricing_campaign(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "campaign_id": args.campaign_id,
    }
    if args.paid_ratio_override is not None:
        body["paid_ratio_override"] = args.paid_ratio_override
    print_json(client_from_args(args).request(
        "POST", "/learning/apply-pricing-campaign", json=body,
    ))


def cmd_promote_strategy(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "goal": args.goal,
        "min_approvals": args.min_approvals,
        "min_age_days": args.min_age_days,
        "dry_run": not args.apply,
        "triggered_by": args.triggered_by,
    }
    result = client_from_args(args).request(
        "POST", "/learning/promote-strategy", json=body,
    )
    print_json(result)
    if result.get("needs_sync_skills"):
        print(
            "\n[!] Wrote skill reference — run `python "
            "playground/learning/sync_skills.py` to push to kol-orchestrator.",
        )


def cmd_backfill_edit_learning(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "dry_run": args.dry_run,
        "limit": args.limit,
    }
    print_json(client_from_args(args).request(
        "POST", "/learning/backfill-edit-learning", json=body,
    ))


def cmd_run_learning_jobs(args: argparse.Namespace) -> None:
    body = {
        "env": args.env,
        "triggered_by": args.triggered_by,
        "limit": args.limit,
        "lookback_days": args.lookback_days,
        "max_results": args.max_results,
        "min_pricing_samples": args.min_pricing_samples,
        "dry_run": args.dry_run,
    }
    if args.suite:
        body["suite"] = args.suite
    if args.jobs:
        body["jobs"] = [j.strip() for j in args.jobs.split(",") if j.strip()]
    print_json(client_from_args(args).request(
        "POST", "/learning/run-scheduled-jobs", json=body,
    ))


def cmd_list_learning_job_runs(args: argparse.Namespace) -> None:
    params = {"limit": args.limit}
    if args.env:
        params["env"] = args.env
    if args.job_name:
        params["job_name"] = args.job_name
    if args.status:
        params["status"] = args.status
    print_json(client_from_args(args).request(
        "GET", "/learning/job-runs", params=params,
    ))


def register(sub: argparse._SubParsersAction) -> None:
    for name, help_text, handler in (
        ("export-fact-corrections", "GET /learning/fact-corrections", cmd_export_fact_corrections),
        ("export-negotiation-history", "GET /learning/negotiation-history", cmd_export_negotiation_history),
        ("export-reject-events", "GET /learning/reject-events", cmd_export_reject_events),
        ("export-edit-events", "GET /learning/edit-events", cmd_export_edit_events),
        ("apply-reject-policy", "POST /learning/apply-reject-policy", cmd_apply_reject_policy),
        ("apply-edit-policy", "POST /learning/apply-edit-policy", cmd_apply_edit_policy),
        ("apply-pricing-campaign", "POST /learning/apply-pricing-campaign", cmd_apply_pricing_campaign),
        (
            "promote-strategy",
            "POST /learning/promote-strategy — promote stable reply_strategy goal into skill ref",
            cmd_promote_strategy,
        ),
        (
            "run-learning-jobs",
            "POST /learning/run-scheduled-jobs — autonomous learning cron entrypoint",
            cmd_run_learning_jobs,
        ),
        (
            "list-learning-job-runs",
            "GET /learning/job-runs — audit trail for learning cron",
            cmd_list_learning_job_runs,
        ),
        (
            "backfill-edit-learning",
            "POST /learning/backfill-edit-learning — backfill draft_edit_learning for sent drafts",
            cmd_backfill_edit_learning,
        ),
    ):
        p = sub.add_parser(name, help=help_text)
        if name in ("run-learning-jobs", "backfill-edit-learning"):
            if name == "run-learning-jobs":
                p.add_argument(
                    "--env",
                    choices=("LIVE",),
                    default="LIVE",
                    help="LIVE only — autonomous learning uses production data.",
                )
            else:
                add_env_arg(p)
                p.add_argument(
                    "--dry-run",
                    action="store_true",
                    help="Preview candidates without writing events.",
                )
        elif name == "list-learning-job-runs":
            p.add_argument("--env", choices=("TEST", "LIVE"), default=None)
        else:
            add_env_arg(p)
        p.add_argument("--identity-id", type=int, default=None)
        if name == "apply-pricing-campaign":
            p.add_argument("--campaign-id", required=True)
            p.add_argument("--paid-ratio-override", type=float, default=None)
        elif name != "run-learning-jobs":
            p.add_argument("--campaign-id", default=None)
        p.add_argument("--goal", default=None)
        p.add_argument("--limit", type=int, default=200)
        if name == "apply-edit-policy":
            p.add_argument(
                "--scope", choices=("company_style", "user_style"), default="company_style",
            )
            p.add_argument("--owner-user-id", type=int, default=None)
        if name in ("apply-reject-policy", "apply-edit-policy"):
            p.add_argument("--updated-by", default="kol_bridge_tool")
        if name == "promote-strategy":
            p.add_argument("--min-approvals", type=int, default=2)
            p.add_argument("--min-age-days", type=int, default=7)
            p.add_argument(
                "--apply",
                action="store_true",
                help="Write the skill reference (default is dry-run preview).",
            )
            p.add_argument("--triggered-by", default="kol_bridge_tool:promote-strategy")
        if name == "run-learning-jobs":
            p.add_argument(
                "--suite",
                choices=("capture", "distill", "pricing", "audit", "nightly", "all"),
                default=None,
                help="Job bundle (default nightly when --jobs omitted)",
            )
            p.add_argument(
                "--jobs",
                default=None,
                help="Comma-separated job names (overrides --suite)",
            )
            p.add_argument("--triggered-by", default="cron:learning")
            p.add_argument("--lookback-days", type=int, default=7)
            p.add_argument("--max-results", type=int, default=100)
            p.add_argument("--min-pricing-samples", type=int, default=3)
            p.add_argument("--dry-run", action="store_true")
        if name == "list-learning-job-runs":
            p.add_argument("--job-name", default=None)
            p.add_argument("--status", choices=("ok", "skipped", "error", "running"), default=None)
        p.set_defaults(func=handler)
