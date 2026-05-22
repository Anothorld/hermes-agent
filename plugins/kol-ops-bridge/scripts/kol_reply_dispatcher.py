#!/usr/bin/env python3
"""Gmail inbound-reply poller → bridge event writer → dispatcher invoker.

Phase B reply pipeline. One-shot or daemon mode. Steps per tick:

1. Query Gmail INBOX (``in:inbox newer_than:<lookback>d``) via the
   bundled ``GmailClient``.
2. For each candidate message, look up the matching outbound event in
   the bridge (by RFC822 ``In-Reply-To`` / ``References`` headers, then
   fallback to ``From:`` lookup against ``kol_facts.contact.gmail``).
3. If matched, POST a ``kol_inbound_reply`` event to the bridge so
   ``kol_conversation_events`` reflects the new turn.
4. Fire ``POST /v1/runs`` against the configured Hermes gateway with a
   skill bundle pointing at ``kol-reply-dispatcher`` and the dispatch
   context for that identity. Watermark (max processed message id) is
   persisted at ``~/.hermes/kol-ops-bridge/poller_state.json``.

Best-effort: unmatched messages are logged and skipped, never queued
for the LLM. If the gateway is unreachable the inbound event is still
written so a later tick (or operator) can resume.

Environment::

    HERMES_KOL_OPS_BRIDGE_BASE   default http://127.0.0.1:8080/api/plugins/kol-ops-bridge
    HERMES_KOL_OPS_BRIDGE_KEY    required for mutating endpoints
    HERMES_GATEWAY_BASE          default http://127.0.0.1:8642
    HERMES_GATEWAY_KEY           Bearer token for /v1/runs
    HERMES_HOME                  default ~/.hermes
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

# Make sibling modules importable when run via `python scripts/foo.py`.
_PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from gmail_client import GmailClient, GmailMessage, GmailUnavailable  # noqa: E402

# scripts/ dir already on sys.path indirectly via this file's location; add
# explicitly so _cal_client resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cal_client import CALClient  # noqa: E402

log = logging.getLogger("kol_reply_dispatcher")

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_PATH = _HERMES_HOME / "kol-ops-bridge" / "poller_state.json"
_BRIDGE = CALClient()
_GATEWAY_BASE = os.environ.get(
    "HERMES_GATEWAY_BASE", "http://127.0.0.1:8642"
).rstrip("/")
_GATEWAY_KEY = os.environ.get("HERMES_GATEWAY_KEY")

# Console-side run registry. Best-effort: failure here must not block reply
# dispatch. The console may not be installed on every host running the
# dispatcher, so we tolerate missing DB or missing table silently.
_CONSOLE_DB_PATH = Path(
    os.environ.get("KOC_DB_PATH")
    or str(Path.home() / ".hermes/kol-ops-console/app.db")
).expanduser()


def _register_console_run(
    *,
    campaign_id: Optional[str],
    env: str,
    run_id: str,
    session_id: str,
) -> None:
    if not campaign_id or not run_id:
        return
    try:
        if not _CONSOLE_DB_PATH.exists():
            return
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        conn = sqlite3.connect(
            str(_CONSOLE_DB_PATH), timeout=5.0, isolation_level=None
        )
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """INSERT OR IGNORE INTO product_campaign_runs
                        (campaign_id, env, run_id, kind, session_id, started_at)
                    VALUES (?,?,?,?,?,?)""",
                (campaign_id, env, run_id, "reply", session_id, now),
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("console run-registry insert skipped: %s", exc)


# ---------------------------------------------------------------- watermark
def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("poller_state unreadable; starting fresh")
        return {}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_STATE_PATH)


# -------------------------------------------------------------------- HTTP
def _http_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    """Gateway-only HTTP helper.  Bridge calls go via :data:`_BRIDGE`."""
    payload: Optional[bytes] = None
    hdrs: dict[str, str] = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"_raw": raw.decode("utf-8", "replace")}


def _gateway_run(*, instructions: str, input_text: str, session_id: str) -> Optional[str]:
    body = {
        "input": input_text,
        "instructions": instructions,
        "session_id": session_id,
        "conversation_history": [],
    }
    headers = {"Authorization": f"Bearer {_GATEWAY_KEY}"} if _GATEWAY_KEY else None
    try:
        out = _http_json(
            "POST",
            f"{_GATEWAY_BASE}/v1/runs",
            headers=headers,
            body=body,
            timeout=30.0,
        )
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        log.error("gateway run failed for %s: %s", session_id, exc)
        return None
    return out.get("run_id") if isinstance(out, dict) else None


# ----------------------------------------------------------------- matching
def _match_identity(
    msg: GmailMessage,
    env: str,
) -> Optional[tuple[int, Optional[str]]]:
    """Return (identity_id, campaign_id) for an inbound msg or None.

    Strategy:
    1. If ``In-Reply-To`` is set, search recent events for a payload
       referencing ``message_id`` / ``thread_id`` and recover identity.
    2. Otherwise return ``None`` — caller logs and skips.
    """
    if not msg.in_reply_to and not msg.thread_id:
        return None
    try:
        page = _BRIDGE.request(
            "GET", "/events/recent", params={"env": env, "limit": 1000},
        )
    except SystemExit as exc:
        log.error("bridge /events/recent failed: %s", exc)
        return None
    events: Iterable[dict[str, Any]] = (page or {}).get("events") or []
    for ev in events:
        if ev.get("env") != env:
            continue
        payload = ev.get("payload") or {}
        if msg.in_reply_to and payload.get("message_id") == msg.in_reply_to:
            return int(ev["identity_id"]), ev.get("campaign_id")
        if msg.thread_id and payload.get("thread_id") == msg.thread_id:
            return int(ev["identity_id"]), ev.get("campaign_id")
    return None


# ---------------------------------------------------------------- main loop
_DISPATCHER_INSTRUCTIONS = (
    "You are running the `kol-reply-dispatcher` skill. Read the supplied "
    "pending_replies array and dispatch context, classify the inbound reply, "
    "persist facts via the bridge CLI, then route to the appropriate child "
    "skill OR open an escalation per the skill's Step 3.5. If a child skill "
    "returns a draft envelope, persist it back to CAL as a `kol_reply_draft_ready` "
    "event and an `approval.reply_draft` fact for operator review. Do not send "
    "mail directly."
)


def _clip_text(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]"


def _dispatch_context(identity_id: int, campaign_id: Optional[str], env: str) -> dict[str, Any]:
    if not campaign_id:
        return {"error": "missing_campaign_id"}
    try:
        return _BRIDGE.request(
            "GET",
            f"/identities/{identity_id}/dispatch-context",
            params={"campaign_id": campaign_id, "env": env},
        )
    except SystemExit as exc:
        log.error("bridge dispatch-context failed for identity=%s campaign=%s: %s",
                  identity_id, campaign_id, exc)
        return {"error": "dispatch_context_unavailable", "detail": str(exc)}


def _pending_reply_payload(
    *,
    msg: GmailMessage,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
) -> dict[str, Any]:
    context = _dispatch_context(identity_id, campaign_id, env)
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "latest_email": {
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "from": msg.from_addr,
            "to": msg.to,
            "subject": msg.subject,
            "date": msg.date,
            "in_reply_to": msg.in_reply_to,
            "references": msg.references,
            "snippet": msg.snippet,
            "body": _clip_text(msg.body),
        },
        "thread_summary": _clip_text(msg.snippet or msg.body, 2000),
        "dispatch_context": context,
    }


ProcessStatus = Literal["dispatched", "skipped", "retry"]


def _process_message(msg: GmailMessage, env: str) -> ProcessStatus:
    """Return whether the message was dispatched, skipped, or should retry."""
    matched = _match_identity(msg, env=env)
    if not matched:
        log.info("[skip] msg=%s no identity match (from=%s)", msg.message_id, msg.from_addr)
        return "skipped"
    identity_id, campaign_id = matched

    event_body = {
        "identity_id": identity_id,
        "event_type": "kol_inbound_reply",
        "actor": "cron",
        "campaign_id": campaign_id,
        "env": env,
        "payload": {
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "in_reply_to": msg.in_reply_to,
            "from_addr": msg.from_addr,
            "subject": msg.subject,
            "snippet": msg.snippet,
            "date": msg.date,
        },
    }
    try:
        _BRIDGE.request("POST", "/events", body=event_body)
    except SystemExit as exc:
        log.error("bridge POST /events failed for msg=%s: %s", msg.message_id, exc)
        return "retry"

    session_id = f"kol-reply:{env}:{identity_id}:{msg.message_id}"
    input_text = json.dumps({
        "pending_replies": [
            _pending_reply_payload(
                msg=msg,
                identity_id=identity_id,
                campaign_id=campaign_id,
                env=env,
            )
        ],
    }, indent=2, ensure_ascii=False)
    run_id = _gateway_run(
        instructions=_DISPATCHER_INSTRUCTIONS,
        input_text=input_text,
        session_id=session_id,
    )
    if not run_id:
        log.error("gateway dispatch did not return run_id for msg=%s", msg.message_id)
        return "retry"
    _register_console_run(
        campaign_id=campaign_id,
        env=env,
        run_id=run_id,
        session_id=session_id,
    )
    log.info("dispatched msg=%s identity=%s campaign=%s run_id=%s",
             msg.message_id, identity_id, campaign_id, run_id)
    return "dispatched"


def run_once(*, env: str, lookback_days: int, max_results: int) -> dict[str, int]:
    client = GmailClient()
    if not client.is_available():
        raise GmailUnavailable("Gmail token / google_api.py unavailable")

    state = _load_state()
    seen: set[str] = set(state.get(f"seen_{env}", []))

    query = f"in:inbox newer_than:{int(lookback_days)}d -from:me"
    messages = client.search(query=query, max_results=max_results)

    matched = 0
    skipped = 0
    retry = 0
    for stub in messages:
        if stub.message_id in seen:
            continue
        try:
            full = client.get_message(stub.message_id)
        except GmailUnavailable as exc:
            log.warning("gmail get %s failed: %s", stub.message_id, exc)
            continue
        status = _process_message(full, env=env)
        if status == "dispatched":
            matched += 1
            seen.add(full.message_id)
        elif status == "skipped":
            skipped += 1
            seen.add(full.message_id)
        else:
            retry += 1

    # Bound the seen-set so the state file doesn't grow forever.
    state[f"seen_{env}"] = sorted(seen)[-2000:]
    state[f"last_run_{env}"] = int(time.time())
    _save_state(state)
    return {"matched": matched, "skipped": skipped, "retry": retry, "scanned": len(messages)}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["TEST", "LIVE"], required=True)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--watch", action="store_true",
                        help="poll forever instead of one-shot")
    parser.add_argument("--interval", type=int, default=60,
                        help="seconds between polls when --watch (default 60)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    def _tick() -> None:
        try:
            stats = run_once(
                env=args.env,
                lookback_days=args.lookback_days,
                max_results=args.max_results,
            )
        except GmailUnavailable as exc:
            log.error("gmail unavailable: %s", exc)
            return
        log.info("tick env=%s stats=%s", args.env, json.dumps(stats))

    _tick()
    while args.watch:
        time.sleep(max(5, args.interval))
        _tick()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
