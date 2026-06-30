#!/usr/bin/env python3
"""Deterministic CLI for cs-ops-bridge (HTTP only, no direct SQLite)."""

from __future__ import annotations

import argparse
import json
import sys
import os
import subprocess
import time
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from _cal_client import (  # noqa: E402
    add_common_args,
    add_env_arg,
    client_from_args,
    print_json,
)
from profile_refs import quickcep_skill_dir  # noqa: E402

# Normalize draft HTML (plugin root for quickcep_cli subprocess import)
_PLUGIN_ROOT_FOR_ENV = _PLUGIN_ROOT
_DEBUG_LOG_PATH = Path("/Users/arnold/agent_prj/.cursor/debug-922c3e.log")

JOIN_CHAT_MAX_ATTEMPTS = 3  # initial + 2 retries
JOIN_CHAT_BACKOFF_BASE_S = 2.0
JOIN_CHAT_SUBPROCESS_TIMEOUT = 130  # getUserInfo 45s + joinChat 60s + margin
_QUICKCEP_CLI_DEFAULT_TIMEOUT = 120


def _debug_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "922c3e",
            "id": f"log_{int(time.time() * 1000)}_{hypothesis_id}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _quickcep_cli_path() -> Path:
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def _run_quickcep_cli(
    cli: Path,
    argv: list[str],
    *,
    timeout: int = _QUICKCEP_CLI_DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("CS_OPS_BRIDGE_PLUGIN_DIR", str(_PLUGIN_ROOT_FOR_ENV))
    return subprocess.run(
        [sys.executable, str(cli), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cli.parent.parent),
        env=env,
    )


def _parse_quickcep_cli_json(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"stdout": stdout}


def _join_chat_error_is_retryable(payload: dict, proc: subprocess.CompletedProcess[str]) -> bool:
    """Retry only on HTTP/socket timeouts (transient QuickCEP slowness)."""
    err = str(payload.get("error") or "")
    step = str(payload.get("failed_step") or "")
    blob = f"{err} {step} {proc.stdout} {proc.stderr}".lower()
    return "timed out" in blob or "timeout" in blob


def _format_join_chat_failure(
    session_id: str,
    proc: subprocess.CompletedProcess[str],
    payload: dict,
    *,
    attempt: int,
) -> dict:
    failed_step = payload.get("failed_step")
    err = str(payload.get("error") or "join-chat failed")
    out: dict = {
        "error": "join-chat failed before draft-save",
        "session_id": session_id,
        "exit_code": proc.returncode,
        "attempt": attempt,
        "max_attempts": JOIN_CHAT_MAX_ATTEMPTS,
        "stderr": proc.stderr,
        "join_chat": payload,
    }
    if failed_step:
        out["failed_step"] = failed_step
        if "timed out" in err.lower():
            out["error_detail"] = f"{failed_step} timed out (QuickCEP HTTP)"
        else:
            out["error_detail"] = f"{failed_step} failed: {err}"
    elif "timed out" in err.lower():
        out["error_detail"] = "join-chat timed out (QuickCEP HTTP)"
    return out


def _join_chat_before_draft(cli: Path, session_id: str) -> dict:
    """QuickCEP requires joinChat before draftMessage/save (same as send-email)."""
    last_failure: dict | None = None
    for attempt in range(1, JOIN_CHAT_MAX_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(JOIN_CHAT_BACKOFF_BASE_S * (2 ** (attempt - 2)))
        proc = _run_quickcep_cli(
            cli,
            ["join-chat", session_id],
            timeout=JOIN_CHAT_SUBPROCESS_TIMEOUT,
        )
        payload = _parse_quickcep_cli_json(proc.stdout)
        if proc.returncode == 0 and payload.get("result_code") in (200, None):
            if not payload.get("failed_step"):
                if attempt > 1:
                    payload["join_chat_attempts"] = attempt
                return payload
        last_failure = _format_join_chat_failure(session_id, proc, payload, attempt=attempt)
        if attempt < JOIN_CHAT_MAX_ATTEMPTS and _join_chat_error_is_retryable(payload, proc):
            continue
        break

    print_json(last_failure or {"error": "join-chat failed before draft-save", "session_id": session_id})
    sys.exit(last_failure.get("exit_code", 1) if last_failure else 1)


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


def _read_optional_file(value: str | None, file_path: str | None) -> str | None:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return value


def _cmd_open_escalation(args: argparse.Namespace) -> None:
    body = {
        "quickcep_session_id": args.session_id,
        "reason": args.reason,
        "urgency": args.urgency,
        "question_to_operator": args.question,
        "customer_email": args.customer_email,
        "email_summary": _read_optional_file(args.email_summary, args.email_summary_file),
        "email_quote": _read_optional_file(args.email_quote, args.email_quote_file),
        "escalation_message": args.message,
        "auto_send_feishu": not args.skip_feishu_send,
        "feishu_chat_id": args.feishu_chat_id,
        "feishu_thread_id": args.feishu_thread_id,
        "feishu_message_id": args.feishu_message_id,
        "resume_context": json.loads(args.resume_context or "{}"),
        "env": args.env,
    }
    print_json(client_from_args(args).request("POST", "/escalations", body=body))


def _cmd_get_messages(args: argparse.Namespace) -> None:
    """Wrap quickcep_cli messages with canonical profile skill path."""
    cli = _quickcep_cli_path()
    if not cli.exists():
        print_json({"error": f"quickcep_cli not found: {cli}"})
        sys.exit(2)

    argv = ["messages", args.session_id]
    if args.page:
        argv.extend(["--page", str(args.page)])
    if args.page_size != 20:
        argv.extend(["--page-size", str(args.page_size)])
    if args.plain:
        argv.append("--plain")
    if args.chronological:
        argv.append("--chronological")

    proc = _run_quickcep_cli(cli, argv)
    if proc.returncode != 0:
        print_json({"error": proc.stderr or proc.stdout, "exit_code": proc.returncode})
        sys.exit(proc.returncode)
    try:
        print_json(json.loads(proc.stdout))
    except json.JSONDecodeError:
        print(proc.stdout, flush=True)


def _fetch_allowed_attachment_urls(args: argparse.Namespace) -> list[str]:
    """Load PDF allow list from bridge HTTP (same DB as serve, works across hosts)."""
    try:
        client = client_from_args(args)
        data = client.request(
            "GET",
            f"/sessions/{args.session_id}/attachment-guard-context",
            query={"env": str(args.env)},
        )
        if isinstance(data, dict):
            return list(data.get("allowed_attachment_urls") or [])
    except SystemExit:
        raise
    except Exception:
        pass
    return []


def _cmd_upload_file(args: argparse.Namespace) -> None:
    """Upload a local file to QuickCEP CDN."""
    path = Path(args.file_path)
    if not path.is_file():
        print_json({"error": f"file not found: {path}"})
        sys.exit(2)
    try:
        from quickcep_cdn import upload_file_to_cdn  # noqa: E402
    except ImportError as exc:
        print_json({"error": f"quickcep_cdn unavailable: {exc}"})
        sys.exit(2)
    result = upload_file_to_cdn(path, feature=args.feature or "email")
    print_json(result)
    sys.exit(0 if result.get("ok") else 2)


def _cmd_draft_save(args: argparse.Namespace) -> None:
    """Wrap quickcep_cli draft-save with join-chat + canonical profile skill path."""
    cli = _quickcep_cli_path()
    if not cli.exists():
        print_json({"error": f"quickcep_cli not found: {cli}"})
        sys.exit(2)
    if args.content_file:
        content_path = Path(args.content_file)
        if not content_path.is_file():
            print_json({"error": f"content file not found: {content_path}"})
            sys.exit(2)
        unsafe_shared_path = content_path.as_posix() == "/tmp/draft.html"
        _debug_log(
            run_id="draft-save",
            hypothesis_id="H1",
            location="cs_bridge_tool.py:_cmd_draft_save",
            message="draft-save content file path observed",
            data={
                "quickcep_session_id": str(args.session_id),
                "content_file": content_path.as_posix(),
                "unsafe_shared_path": unsafe_shared_path,
            },
        )
        if unsafe_shared_path:
            print_json(
                {
                    "error": "unsafe shared content-file path /tmp/draft.html",
                    "error_detail": "use a session-scoped path like /tmp/draft-<session_id>.html",
                    "session_id": args.session_id,
                }
            )
            sys.exit(2)
        content = content_path.read_text(encoding="utf-8")
    else:
        content = args.content

    try:
        from draft_html import normalize_draft_html  # noqa: E402 — plugin root on sys.path
    except ImportError:
        normalize_draft_html = None
    if normalize_draft_html is not None:
        content = normalize_draft_html(content)

    # Internal domain guard — block drafts containing internal/backend URLs
    try:
        from internal_domain_guard import guard_draft  # noqa: E402 — plugin root on sys.path
    except ImportError:
        guard_draft = None
    if guard_draft is not None:
        guard_result = guard_draft(content, getattr(args, "attachments", None))
        if guard_result["blocked"]:
            print_json(
                {
                    "error": guard_result["error"],
                    "error_detail": f"Matched: {', '.join(guard_result['matches'])}",
                    "source": guard_result["source"],
                    "snippet": guard_result["snippet"],
                    "session_id": args.session_id,
                }
            )
            sys.exit(2)

    # PDF attachment guard — only vault-sourced PDFs on escalation resume
    try:
        from draft_attachment_guard import attachments_contain_pdf, guard_draft_attachments  # noqa: E402
    except ImportError:
        attachments_contain_pdf = None  # type: ignore[assignment,misc]
        guard_draft_attachments = None  # type: ignore[assignment,misc]
    attachments_json = getattr(args, "attachments", None)
    if not isinstance(attachments_json, str):
        attachments_json = None
    if attachments_contain_pdf is not None and guard_draft_attachments is not None:
        if attachments_contain_pdf(attachments_json):
            allowed_urls = _fetch_allowed_attachment_urls(args)
            att_guard = guard_draft_attachments(
                attachments_json,
                allowed_attachment_urls=allowed_urls,
            )
            if att_guard["blocked"]:
                print_json(
                    {
                        "error": att_guard["error"],
                        "error_detail": att_guard.get("error_detail", ""),
                        "source": att_guard.get("source", "attachments"),
                        "blocked_kind": att_guard.get("blocked_kind", ""),
                        "session_id": args.session_id,
                    }
                )
                sys.exit(2)

    join_chat = _join_chat_before_draft(cli, args.session_id)

    draft_argv = [
        "draft-save",
        args.session_id,
        "--content",
        content,
    ]
    if args.subject:
        draft_argv.extend(["--subject", args.subject])
    if args.receiver:
        draft_argv.extend(["--receiver", args.receiver])
    if getattr(args, "attachments", None):
        draft_argv.extend(["--attachments", attachments_json or args.attachments])
    proc = _run_quickcep_cli(cli, draft_argv)
    if proc.returncode != 0:
        print_json({"error": proc.stderr or proc.stdout, "exit_code": proc.returncode, "join_chat": join_chat})
        sys.exit(proc.returncode)
    try:
        result = json.loads(proc.stdout)
        result["join_chat"] = join_chat
        print_json(result)
    except json.JSONDecodeError:
        print(proc.stdout, flush=True)


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

    gm = sub.add_parser("get-messages", help="Read QuickCEP session messages (wraps quickcep_cli messages)")
    add_env_arg(gm)
    gm.add_argument("--session-id", required=True)
    gm.add_argument("--page", type=int, default=0, help="pageIndex (0-based, default: 0)")
    gm.add_argument("--page-size", type=int, default=20, help="Page size (default: 20)")
    gm.add_argument("--plain", action=argparse.BooleanOptionalAction, default=True, help="Strip HTML (default: true)")
    gm.add_argument(
        "--chronological",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Oldest-first order (default: true)",
    )
    gm.set_defaults(func=_cmd_get_messages)

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
    o.add_argument("--urgency", default="medium", choices=("high", "medium", "low"))
    o.add_argument("--question", default=None, help="Question for the expert group")
    o.add_argument(
        "--customer-email",
        default=None,
        help="Customer email from get-messages (required for Feishu notify unless --message)",
    )
    o.add_argument(
        "--email-summary",
        default=None,
        help="Simplified Chinese summary of relevant customer email (required for Feishu notify)",
    )
    o.add_argument(
        "--email-summary-file",
        default=None,
        help="Read --email-summary from file",
    )
    o.add_argument(
        "--email-quote",
        default=None,
        help="Customer's full original email text (required for Feishu notify)",
    )
    o.add_argument(
        "--email-quote-file",
        default=None,
        help="Read --email-quote from file (preferred for full email text)",
    )
    o.add_argument(
        "--message",
        default=None,
        help=(
            "Full Feishu message body override. Skips bridge template: no auto 📦 order block, "
            "and --email-summary/--email-quote are not required. Include order info manually."
        ),
    )
    o.add_argument("--skip-feishu-send", action="store_true", help="Record escalation only; do not post to Feishu")
    o.add_argument("--feishu-chat-id", default=None)
    o.add_argument("--feishu-thread-id", default=None)
    o.add_argument("--feishu-message-id", default=None)
    o.add_argument("--resume-context", default="{}")
    o.set_defaults(func=_cmd_open_escalation)

    ds = sub.add_parser("draft-save", help="Save QuickCEP draft (wraps quickcep_cli draft-save)")
    add_env_arg(ds)
    ds.add_argument("--session-id", required=True)
    content_src = ds.add_mutually_exclusive_group(required=True)
    content_src.add_argument(
        "--content",
        help="Draft body (HTML or plain). In shell, use single quotes if text contains $.",
    )
    content_src.add_argument(
        "--content-file",
        help="Read draft body from file (preferred when content contains $ amounts); use session-scoped paths like /tmp/draft-<session_id>.html.",
    )
    ds.add_argument("--subject", default=None)
    ds.add_argument("--receiver", default=None)
    ds.add_argument(
        "--attachments",
        default=None,
        help='JSON array of attachment objects (same as quickcep_cli draft-save --attachments)',
    )
    ds.set_defaults(func=_cmd_draft_save)

    uf = sub.add_parser("upload-file", help="Upload local file to QuickCEP CDN")
    uf.add_argument("file_path", help="Path to file on disk")
    uf.add_argument(
        "--feature",
        default="email",
        choices=("email", "send-image"),
        help="QuickCEP upload feature (email for draft attachments)",
    )
    uf.set_defaults(func=_cmd_upload_file)

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
