#!/usr/bin/env python3
"""Backfill leave-chat for terminal-status sessions the AI account never left.

Background
----------
Before `close_session.py` recorded `quickcep_leave_chat` CAL events (and before
the `leave-on-failed-handoff` fix shipped), every `join-chat` on the inbound
launch path had no matching `leave-chat` in CAL. The AI account accumulated
1000+ "stuck" sessions in QuickCEP — visible as 200+ assigned in the workbench —
even though most were `reviewed` / `failed` / `skipped` (terminal) and should
have been left long ago.

This script scans CAL for terminal-status sessions where joins > leaves and
calls `quickcep_cli leave-chat --token <jwt>` to unassign the AI account, then
records a `quickcep_leave_chat` CAL event (`source=backfill`) so future audits
see the leave.

Scope
-----
- READ-ONLY on cal.db for candidate discovery (SELECT only).
- Only touches sessions in TERMINAL_STATUSES (reviewed / failed / skipped /
  closed). NEVER touches processing / pending / draft_ready / awaiting_expert /
  operator_replied — those are active.
- Uses the AI account's cached QuickCEP token (`.quickcep_token.json` next to
  `quickcep_cli.py`). Passes `--token` so the cache is never overwritten.
- Dry-run by default; requires `--apply` to actually call leave-chat.
- Gentle rate limit (default 0.5s between leaves) to avoid QuickCEP 429.
- Fail-soft per session: one leave failure does not abort the batch.

Usage
-----
Run inside the povison-cs-bridge container (has cal.db + quickcep_cli + token):

    docker exec povison-cs-bridge /opt/hermes/.venv/bin/python \
        /opt/hermes/plugins/cs-ops-bridge/scripts/leave_chat_backfill.py \
        --dry-run --limit 50

    # Actually leave (writes CAL events + calls QuickCEP)
    docker exec povison-cs-bridge /opt/hermes/.venv/bin/python \
        /opt/hermes/plugins/cs-ops-bridge/scripts/leave_chat_backfill.py \
        --apply --limit 100

Flags:
  --dry-run          (default) Don't call leave-chat; just print what would happen
  --apply            Actually call leave-chat and record CAL events
  --limit N          Max sessions to process (default 50)
  --env ENV          CAL env (default LIVE)
  --sleep SECONDS    Delay between leaves (default 0.5)
  --status S,S,...   Override terminal statuses (default reviewed,failed,skipped,closed)

Exit codes: 0 = success (incl. partial failures), 1 = setup error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

# Make plugin imports work whether run as `python scripts/leave_chat_backfill.py`
# (plugin root on sys.path) or `python -m plugins.cs-ops-bridge.scripts...`.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

try:
    from .profile_refs import quickcep_skill_dir  # type: ignore
except ImportError:  # pragma: no cover — direct script invocation
    from profile_refs import quickcep_skill_dir  # type: ignore

# pii_sanitize has no package-relative imports, so it loads standalone.
try:
    from .pii_sanitize import sanitize_mapping  # type: ignore
except ImportError:  # pragma: no cover — direct script invocation
    from pii_sanitize import sanitize_mapping  # type: ignore

# NOTE: this script intentionally does NOT import the plugin's `cal` module.
# `cal.py` uses package-relative imports (`from .schema import ...`) that only
# resolve when loaded as part of the plugin package (inside the bridge
# container's plugin loader). Running `python scripts/leave_chat_backfill.py`
# standalone would fail on that import. Instead we open the same SQLite db
# path directly — the schema is stable (cs_session, cs_conversation_events)
# and the operations here are plain SELECT/INSERT that don't need cal's
# helpers. The db path resolution mirrors cal._DB_PATH exactly.
_DB_PATH = Path(
    os.environ.get(
        "HERMES_CS_OPS_CAL_DB",
        Path(os.path.expanduser("~/.hermes/cs-ops-bridge/cal.db")),
    )
)

log = logging.getLogger("leave_chat_backfill")

TERMINAL_STATUSES = ("reviewed", "failed", "skipped", "closed")
LEAVE_CHAT_SUBPROCESS_TIMEOUT = 120


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _db_connect() -> sqlite3.Connection:
    """Open the CAL db read/write. Mirrors cal._connect (busy_timeout, row_factory)."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _quickcep_cli_path() -> Path:
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def _token_path() -> Path:
    return _quickcep_cli_path().parent / ".quickcep_token.json"


def _load_ai_token() -> tuple[str, str]:
    """Return (jwt, operator_user_id) from the AI account's cached token.

    Raises RuntimeError if the cache is missing or the JWT cannot be decoded.
    """
    import base64

    tp = _token_path()
    if not tp.is_file():
        raise RuntimeError(
            f"QuickCEP token cache not found at {tp}. The AI account must have "
            "logged in at least once so the token is cached."
        )
    with open(tp, encoding="utf-8") as f:
        data = json.load(f)
    jwt = data.get("jwt") or data.get("token") or ""
    if not jwt:
        raise RuntimeError(f"Token cache at {tp} has no jwt/token field.")
    parts = jwt.split(".")
    if len(parts) < 2:
        raise RuntimeError("Cached JWT is malformed (no payload segment).")
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as exc:  # noqa: BLE001 — JWT decode surface is wide
        raise RuntimeError(f"Failed to decode JWT payload: {exc}") from exc
    user_id = str(claims.get("userId") or "")
    if not user_id:
        raise RuntimeError("JWT payload has no userId — cannot identify AI account.")
    return jwt, user_id


def _run_quickcep_cli(argv: list[str], *, timeout: int = LEAVE_CHAT_SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run quickcep_cli.py with the plugin root env (mirrors quickcep_join._run_quickcep_cli)."""
    cli = _quickcep_cli_path()
    env = os.environ.copy()
    env.setdefault("CS_OPS_BRIDGE_PLUGIN_DIR", str(_PLUGIN_ROOT))
    return subprocess.run(
        [sys.executable, str(cli), *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cli.parent.parent),
        env=env,
        check=False,
    )


def _call_leave_chat(session_id: str, jwt: str) -> dict[str, Any]:
    """Call quickcep_cli leave-chat --token <jwt> for one session. Best-effort."""
    try:
        proc = _run_quickcep_cli(["leave-chat", session_id, "--token", jwt])
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "exit_code": None}
    except Exception as exc:  # noqa: BLE001 — per-session isolation
        return {"ok": False, "error": str(exc), "exit_code": None}
    stdout = (proc.stdout or "").strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        payload = {"raw_stdout": stdout[:500]}
    # Reconcile email-leaveChat vs live-chat_end (same as close_session).
    # `quickcep_leave_confirm` is a standalone module (no relative imports) —
    # _PLUGIN_ROOT is on sys.path so this resolves directly.
    from quickcep_leave_confirm import reconcile_leave_chat_payload  # type: ignore
    payload = reconcile_leave_chat_payload(
        payload, cli=_quickcep_cli_path(), session_id=session_id,
    )
    ok = proc.returncode == 0 and bool(payload.get("ok"))
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "result_code": payload.get("result_code"),
        "error": payload.get("error"),
        "confirmed_via": payload.get("confirmed_via"),
        "stderr": (proc.stderr or "")[:500],
    }


def _record_leave_event(
    *, quickcep_session_id: str, env: str, leave_result: dict[str, Any]
) -> None:
    """Write a quickcep_leave_chat CAL event (source=backfill). Fail-soft.

    Mirrors cal.write_event's INSERT shape for cs_conversation_events. Does NOT
    use cal.write_event because that requires importing the plugin package
    (relative imports). The INSERT is idempotent at the row level — one event
    per call, matching close_session / leave_quickcep_after_terminal_handoff.
    """
    payload = {
        "source": "backfill",
        "ok": leave_result.get("ok", False),
        "exit_code": leave_result.get("exit_code"),
        "result_code": leave_result.get("result_code"),
        "error": leave_result.get("error"),
        "confirmed_via": leave_result.get("confirmed_via"),
    }
    # Apply the same PII redaction cal.write_event uses (pii_sanitize.sanitize_mapping).
    # We can't call cal.write_event directly because it requires importing the plugin
    # package (relative imports), so we mirror its INSERT + sanitize here.
    safe_payload = sanitize_mapping(payload)
    try:
        with _db_connect() as conn:
            sess = conn.execute(
                "SELECT id FROM cs_session WHERE quickcep_session_id=? AND env=?",
                (quickcep_session_id, env),
            ).fetchone()
            if not sess:
                log.warning(
                    "backfill: session not in CAL, cannot write leave event sid=%s env=%s",
                    quickcep_session_id, env,
                )
                return
            conn.execute(
                "INSERT INTO cs_conversation_events(session_id, event_type, payload_json, env, created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    sess["id"],
                    "quickcep_leave_chat",
                    json.dumps(safe_payload, ensure_ascii=False),
                    env,
                    _now_iso(),
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — audit must not block
        log.warning(
            "backfill: quickcep_leave_chat event write failed session=%s: %s",
            quickcep_session_id,
            exc,
        )


def _discover_candidates(env: str, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return terminal-status sessions where joins > leaves (AI still stuck in).

    READ-ONLY SELECT on cal.db. Mirrors the audit query used to find the
    1159 net-stuck sessions in the 2026-07-30 incident.
    """
    placeholders = ",".join("?" for _ in statuses)
    with _db_connect() as conn:
        rows = list(conn.execute(
            f"""
            SELECT
                s.quickcep_session_id AS sid,
                s.status AS status,
                s.created_at AS created,
                s.updated_at AS updated,
                SUM(CASE WHEN e.event_type='quickcep_join_chat' THEN 1 ELSE 0 END) AS joins,
                SUM(CASE WHEN e.event_type='quickcep_leave_chat' THEN 1 ELSE 0 END) AS leaves
            FROM cs_session s
            LEFT JOIN cs_conversation_events e
                ON e.session_id = s.id
               AND e.event_type IN ('quickcep_join_chat','quickcep_leave_chat')
            WHERE s.env = ?
              AND s.status IN ({placeholders})
            GROUP BY s.id
            HAVING joins > leaves
            ORDER BY s.updated_at ASC
            """,
            (env, *statuses),
        ))
    return [
        {
            "session_id": r["sid"],
            "status": r["status"],
            "joins": r["joins"],
            "leaves": r["leaves"],
            "updated_at": r["updated"],
        }
        for r in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Backfill leave-chat for terminal sessions the AI account never left.",
    )
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="Don't call leave-chat; just print candidates (default behavior).")
    ap.add_argument("--apply", action="store_true", default=False,
                    help="Actually call leave-chat and record CAL events.")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max sessions to process (default 50).")
    ap.add_argument("--env", default="LIVE", help="CAL env (default LIVE).")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="Delay between leave calls in seconds (default 0.5).")
    ap.add_argument("--status", default=",".join(TERMINAL_STATUSES),
                    help=f"Comma-separated terminal statuses "
                         f"(default {','.join(TERMINAL_STATUSES)}).")
    args = ap.parse_args()

    if args.apply and args.dry_run:
        print("error: --apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
    if not statuses:
        print("error: --status produced no valid statuses", file=sys.stderr)
        return 1

    log.info("backfill: env=%s statuses=%s apply=%s limit=%d",
             args.env, statuses, args.apply, args.limit)

    # Pre-flight: confirm cal.db and token are reachable BEFORE we claim anything.
    try:
        candidates = _discover_candidates(args.env, statuses)
    except Exception as exc:  # noqa: BLE001 — db/SQL errors are wide
        log.error("backfill: candidate discovery failed: %s", exc)
        return 1
    log.info("backfill: discovered %d candidate sessions (joins>leaves, terminal)",
             len(candidates))

    if not candidates:
        print(json.dumps({
            "ok": True,
            "env": args.env,
            "statuses": list(statuses),
            "candidates": 0,
            "applied": False,
            "message": "No terminal sessions with joins>leaves found. Nothing to backfill.",
        }, ensure_ascii=False, indent=2))
        return 0

    to_process = candidates[: args.limit]
    print(f"Candidates: {len(candidates)} total, processing first {len(to_process)} "
          f"(limit={args.limit})", file=sys.stderr)

    if not args.apply:
        print(json.dumps({
            "ok": True,
            "env": args.env,
            "statuses": list(statuses),
            "candidates_total": len(candidates),
            "processing": len(to_process),
            "applied": False,
            "dry_run": True,
            "sessions": to_process,
            "message": "Dry run — no leave-chat calls made. Re-run with --apply to process.",
        }, ensure_ascii=False, indent=2))
        return 0

    # --apply path: load AI token, then leave each session.
    try:
        jwt, ai_user_id = _load_ai_token()
    except RuntimeError as exc:
        log.error("backfill: token load failed: %s", exc)
        return 1
    log.info("backfill: using AI account userId=%s", ai_user_id)

    results: list[dict[str, Any]] = []
    ok_count = 0
    fail_count = 0
    for i, c in enumerate(to_process, 1):
        sid = c["session_id"]
        log.info("backfill: [%d/%d] leave session=%s status=%s joins=%d leaves=%d",
                 i, len(to_process), sid, c["status"], c["joins"], c["leaves"])
        leave = _call_leave_chat(sid, jwt)
        _record_leave_event(
            quickcep_session_id=sid, env=args.env, leave_result=leave,
        )
        if leave.get("ok"):
            ok_count += 1
        else:
            fail_count += 1
        results.append({
            "session_id": sid,
            "status": c["status"],
            "joins": c["joins"],
            "leaves_before": c["leaves"],
            "leave_ok": leave.get("ok", False),
            "leave_error": leave.get("error"),
            "confirmed_via": leave.get("confirmed_via"),
        })
        if i < len(to_process):
            time.sleep(args.sleep)

    print(json.dumps({
        "ok": fail_count == 0,
        "env": args.env,
        "statuses": list(statuses),
        "candidates_total": len(candidates),
        "processed": len(to_process),
        "applied": True,
        "left_ok": ok_count,
        "failed": fail_count,
        "sessions": results,
    }, ensure_ascii=False, indent=2))
    log.info("backfill: done ok=%d fail=%d of %d processed", ok_count, fail_count, len(to_process))
    return 0


if __name__ == "__main__":
    sys.exit(main())
