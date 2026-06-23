#!/usr/bin/env python3
"""Gmail inbound-reply poller → bridge event writer → dispatcher invoker.

Phase B reply pipeline. One-shot or daemon mode. Steps per tick:

1. Query Gmail INBOX (``in:inbox newer_than:<lookback>d``) via the
   bundled ``GmailClient``.
2. For each candidate message, resolve identity/campaign through a
   deterministic matcher: strict RFC822 ``In-Reply-To`` hit, then
   ``thread_id`` hit, then detached-thread heuristic (sender+subject+time
   window). Emit anomaly signals (thread integrity, identity integrity,
   risk controls) for downstream soft-gating.
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
import contextlib
import datetime as _dt
import errno
import fcntl
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Optional

# Register plugin as a package so relative imports in sibling modules work
# when this file is executed as a script (reply watcher / one-shot poller).
_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_PLUGIN_PKG = "kol_ops_bridge_pkg"
if _PLUGIN_PKG not in sys.modules:
    import types

    _pkg = types.ModuleType(_PLUGIN_PKG)
    _pkg.__path__ = [str(_PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PLUGIN_PKG] = _pkg

from kol_ops_bridge_pkg.gmail_client import GmailClient, GmailMessage, GmailUnavailable  # noqa: E402
from kol_ops_bridge_pkg.gmail_console import list_operator_gmail_clients  # noqa: E402
from kol_ops_bridge_pkg.mailbox_escalation import ensure_mailbox_mismatch_escalation  # noqa: E402

# scripts/ dir already on sys.path indirectly via this file's location; add
# explicitly so _cal_client resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cal_client import CALClient  # noqa: E402

log = logging.getLogger("kol_reply_dispatcher")

# region agent log
_DEBUG_LOG_PATH = os.environ.get("KOL_OPS_INBOUND_DEBUG_LOG", "").strip()


def _agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    run_id: str = "pre-fix",
) -> None:
    if not _DEBUG_LOG_PATH:
        return
    try:
        payload = {
            "sessionId": "d9f87f",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
            "runId": run_id,
        }
        with Path(_DEBUG_LOG_PATH).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


# endregion

class _MatchBridgeError(RuntimeError):
    """Bridge unavailable while matching inbound mail to an identity."""

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_STATE_PATH = _HERMES_HOME / "kol-ops-bridge" / "poller_state.json"
_LOCK_PATH = _STATE_PATH.with_suffix(".lock")
_BRIDGE = CALClient()
_GATEWAY_BASE = os.environ.get(
    "HERMES_GATEWAY_BASE", "http://127.0.0.1:8642"
).rstrip("/")
_GATEWAY_KEY = os.environ.get("HERMES_GATEWAY_KEY")
_HERMES_AGENT_ROOT = Path(__file__).resolve().parents[3]
_BRIDGE_CLI_ABS = (
    _HERMES_AGENT_ROOT / "plugins/kol-ops-bridge/scripts/kol_bridge_tool.py"
)
_DETACHED_MATCH_WINDOW_DAYS = 14
_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com",
    "live.com", "proton.me", "protonmail.com",
}
_AGENCY_CUE_RE = re.compile(
    r"\b(agent|agency|management|manager|assistant|team|talent|rep|representative)\b",
    re.IGNORECASE,
)
_PAYMENT_CUE_RE = re.compile(
    r"\b(paypal|wire|bank|swift|iban|payoneer|stripe|crypto|usdt|wallet|invoice|payout)\b",
    re.IGNORECASE,
)
_CONTRACT_CUE_RE = re.compile(
    r"\b(contract|agreement|msa|nda|clause|term[s]?)\b",
    re.IGNORECASE,
)
_BUDGET_CUE_RE = re.compile(
    r"\b(rate|budget|quote|quoted|price|pricing|paid|commission|compensation)\b",
    re.IGNORECASE,
)
_HANDOFF_CUE_RE = re.compile(
    r"\b(contact|reach out|coordinate|follow up).{0,50}\b(agent|manager|assistant|team)\b",
    re.IGNORECASE,
)

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


def _console_db_connect() -> sqlite3.Connection | None:
    try:
        if not _CONSOLE_DB_PATH.exists():
            return None
        return sqlite3.connect(
            str(_CONSOLE_DB_PATH), timeout=5.0, isolation_level=None
        )
    except sqlite3.Error:
        return None


def _global_message_seen(*, env: str, message_id: str) -> bool:
    conn = _console_db_connect()
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM gmail_poller_global_seen WHERE env=? AND message_id=? LIMIT 1",
            (env, message_id),
        ).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        log.warning("global seen lookup skipped: %s", exc)
        return False
    finally:
        conn.close()


def _record_global_message_seen(
    *, env: str, message_id: str, mailbox_user_id: int
) -> None:
    conn = _console_db_connect()
    if conn is None:
        return
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """INSERT OR IGNORE INTO gmail_poller_global_seen
                    (env, message_id, mailbox_user_id, seen_at)
                VALUES (?,?,?,?)""",
            (env, message_id, int(mailbox_user_id), now),
        )
        conn.execute(
            """INSERT INTO gmail_poller_watermarks (user_id, last_message_id, updated_at)
                VALUES (?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_message_id=excluded.last_message_id,
                    updated_at=excluded.updated_at""",
            (int(mailbox_user_id), message_id, now),
        )
    except sqlite3.Error as exc:
        log.warning("global seen / watermark write skipped: %s", exc)
    finally:
        conn.close()


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


def _retry_backoff_bucket_key(env: str) -> str:
    return f"retry_backoff_{env}"


def _retry_failures_key(env: str) -> str:
    return f"retry_failures_{env}"


def _gateway_retry_base_sec() -> int:
    return max(15, int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_RETRY_BASE_SEC", "60")))


def _gateway_retry_max_sec() -> int:
    return max(_gateway_retry_base_sec(), int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_RETRY_MAX_SEC", "3600")))


def _retry_not_before(state: dict[str, Any], *, env: str, message_id: str) -> float:
    bucket = state.get(_retry_backoff_bucket_key(env), {})
    if not isinstance(bucket, dict):
        return 0.0
    try:
        return float(bucket.get(message_id) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _record_retry_backoff(state: dict[str, Any], *, env: str, message_id: str) -> None:
    failures_bucket = state.setdefault(_retry_failures_key(env), {})
    if not isinstance(failures_bucket, dict):
        failures_bucket = {}
        state[_retry_failures_key(env)] = failures_bucket
    failures = int(failures_bucket.get(message_id, 0)) + 1
    failures_bucket[message_id] = failures
    delay = min(
        _gateway_retry_max_sec(),
        _gateway_retry_base_sec() * (2 ** min(failures - 1, 6)),
    )
    backoff_bucket = state.setdefault(_retry_backoff_bucket_key(env), {})
    if not isinstance(backoff_bucket, dict):
        backoff_bucket = {}
        state[_retry_backoff_bucket_key(env)] = backoff_bucket
    backoff_bucket[message_id] = time.time() + delay


def _clear_retry_backoff(state: dict[str, Any], *, env: str, message_id: str) -> None:
    for key in (_retry_backoff_bucket_key(env), _retry_failures_key(env)):
        bucket = state.get(key)
        if isinstance(bucket, dict):
            bucket.pop(message_id, None)


def _needs_reprocess_after_global_seen(msg: GmailMessage, *, env: str) -> bool:
    try:
        matched = _match_identity(msg, env=env)
    except _MatchBridgeError:
        return True
    if not matched or not matched.campaign_id:
        return False
    try:
        dispatch_status = _BRIDGE.request(
            "GET",
            f"/identities/{matched.identity_id}/reply-dispatch-status",
            params={
                "campaign_id": matched.campaign_id,
                "message_id": msg.message_id,
                "env": env,
            },
        )
    except SystemExit:
        return True
    if not isinstance(dispatch_status, dict):
        return False
    if dispatch_status.get("should_retry_gateway_only"):
        return True
    if dispatch_status.get("should_skip_poller"):
        return False
    return False


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
@dataclass(frozen=True)
class IdentityMatch:
    identity_id: int
    campaign_id: Optional[str]
    thread_integrity: str
    matched_by: str
    history_thread_id: Optional[str]
    identity_integrity: str
    reasons: list[str]
    content_risk: str
    risk_controls: dict[str, bool]
    sender_email: Optional[str]
    expected_email: Optional[str]


def _extract_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    _, addr = parseaddr(value)
    addr = (addr or "").strip().lower()
    return addr or None


def _email_domain(value: Optional[str]) -> Optional[str]:
    if not value or "@" not in value:
        return None
    return value.rsplit("@", 1)[-1].strip().lower() or None


def _normalize_subject(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    while raw.startswith(("re:", "fw:", "fwd:")):
        raw = raw.split(":", 1)[-1].strip()
    return raw


def _event_message_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("message_id", "source_message_id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            ids.add(val)
    for key in ("draft", "gmail_draft"):
        block = payload.get(key)
        if isinstance(block, dict):
            val = block.get("message_id")
            if isinstance(val, str) and val:
                ids.add(val)
    return ids


def _event_thread_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    val = payload.get("thread_id")
    if isinstance(val, str) and val:
        ids.add(val)
    for key in ("draft", "gmail_draft"):
        block = payload.get(key)
        if isinstance(block, dict):
            t = block.get("thread_id")
            if isinstance(t, str) and t:
                ids.add(t)
    return ids


def _event_emails(payload: dict[str, Any]) -> set[str]:
    emails: set[str] = set()
    for key in ("to", "from", "from_addr"):
        parsed = _extract_email(payload.get(key) if isinstance(payload.get(key), str) else None)
        if parsed:
            emails.add(parsed)
    draft = payload.get("draft")
    if isinstance(draft, dict):
        parsed = _extract_email(draft.get("to") if isinstance(draft.get("to"), str) else None)
        if parsed:
            emails.add(parsed)
    return emails


def _event_subject(payload: dict[str, Any]) -> Optional[str]:
    for key in ("subject",):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    draft = payload.get("draft")
    if isinstance(draft, dict):
        value = draft.get("subject")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_event_timestamp(raw: Any) -> Optional[_dt.datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except ValueError:
        return None


def _derive_content_risk(msg: GmailMessage) -> tuple[str, dict[str, bool]]:
    haystack = f"{msg.subject}\n{msg.body}".lower()
    gate_budget = bool(_BUDGET_CUE_RE.search(haystack))
    gate_contract = bool(_CONTRACT_CUE_RE.search(haystack))
    gate_payout = bool(_PAYMENT_CUE_RE.search(haystack))
    if _HANDOFF_CUE_RE.search(haystack):
        risk = "c3"
        gate_budget = True
        gate_contract = True
        gate_payout = True
    elif gate_budget or gate_contract or gate_payout:
        risk = "c2"
    else:
        risk = "c1"
    return risk, {
        "gate_budget": gate_budget,
        "gate_contract": gate_contract,
        "gate_payout": gate_payout,
    }


def resolve_autoflow_controls(
    *,
    content_risk: str,
    thread_integrity: str,
    identity_integrity: str,
    controls: dict[str, bool],
) -> tuple[bool, dict[str, bool]]:
    """Return ``(allow_autoflow, updated_controls)`` for reply soft-gating."""
    out = dict(controls)
    allow_autoflow = True
    if content_risk == "c3":
        allow_autoflow = False
    elif thread_integrity == "detached" and (
        out["gate_budget"] or out["gate_contract"] or out["gate_payout"]
    ):
        allow_autoflow = False
    elif (
        identity_integrity == "delegated"
        and out["gate_budget"]
        and not (out["gate_contract"] or out["gate_payout"])
        and thread_integrity != "detached"
    ):
        # Agency/manager rep discussing rates — allow compensation negotiation.
        allow_autoflow = True
        out["gate_budget"] = False
    elif identity_integrity in {"delegated", "unknown"} and (
        out["gate_budget"] or out["gate_contract"] or out["gate_payout"]
    ):
        allow_autoflow = False
    return allow_autoflow, out


def _classify_identity_integrity(
    *,
    sender_email: Optional[str],
    expected_email: Optional[str],
    from_header: str,
    body: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not expected_email:
        reasons.append("identity_primary_email_missing")
        return "unknown", reasons
    if sender_email == expected_email:
        return "matched", reasons
    sender_domain = _email_domain(sender_email)
    expected_domain = _email_domain(expected_email)
    if sender_domain and expected_domain and sender_domain == expected_domain:
        if sender_domain in _PERSONAL_EMAIL_DOMAINS:
            reasons.append("same_provider_domain_not_authoritative")
            return "drifted", reasons
        reasons.append("same_domain_alias")
        return "drifted", reasons
    if _AGENCY_CUE_RE.search(from_header) or _AGENCY_CUE_RE.search(body):
        reasons.append("agency_cue_detected")
        return "delegated", reasons
    reasons.append("sender_email_mismatch")
    return "drifted", reasons


def _expected_identity_email(identity_id: int) -> Optional[str]:
    try:
        identity = _BRIDGE.request("GET", f"/identities/{identity_id}")
    except SystemExit:
        return None
    if not isinstance(identity, dict):
        return None
    primary = identity.get("primary_email")
    if not isinstance(primary, str):
        return None
    normalized = primary.strip().lower()
    return normalized or None


def _match_identity(
    msg: GmailMessage,
    env: str,
) -> Optional[IdentityMatch]:
    """Return enriched identity-match context for an inbound msg or None."""
    try:
        page = _BRIDGE.request(
            "GET", "/events/recent", params={"env": env, "limit": 1000},
        )
    except SystemExit as exc:
        log.error("bridge /events/recent failed: %s", exc)
        raise _MatchBridgeError(f"list_recent_events failed: {exc}") from exc
    events_list: list[dict[str, Any]] = list((page or {}).get("events") or [])
    events: Iterable[dict[str, Any]] = events_list
    strict_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    weak_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    sender_email = _extract_email(msg.from_addr)
    norm_subject = _normalize_subject(msg.subject)
    now = _dt.datetime.now(_dt.timezone.utc)
    best_detached_score = -1
    detached_hit: Optional[tuple[int, Optional[str], str, str, Optional[str]]] = None
    detached_candidates: dict[tuple[int, Optional[str]], dict[str, Any]] = {}

    for ev in events:
        if ev.get("env") != env:
            continue
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if not isinstance(ev.get("identity_id"), int):
            continue
        identity_id = int(ev["identity_id"])
        campaign_id = ev.get("campaign_id")

        event_message_ids = _event_message_ids(payload)
        event_thread_ids = _event_thread_ids(payload)
        canonical_thread_id = sorted(event_thread_ids)[0] if event_thread_ids else None
        if msg.in_reply_to and msg.in_reply_to in event_message_ids:
            strict_hit = (
                identity_id,
                campaign_id,
                "strict",
                "in_reply_to",
                canonical_thread_id or msg.thread_id or None,
            )
            break
        if msg.thread_id and msg.thread_id in event_thread_ids and weak_hit is None:
            weak_hit = (
                identity_id,
                campaign_id,
                "weak",
                "thread_id",
                msg.thread_id,
            )

        if not sender_email:
            continue
        event_emails = _event_emails(payload)
        if sender_email not in event_emails:
            continue
        score = 2
        event_subject = _normalize_subject(_event_subject(payload))
        if norm_subject and event_subject and norm_subject == event_subject:
            score += 1
        event_dt = _parse_event_timestamp(ev.get("created_at") or ev.get("captured_at"))
        if event_dt and (now - event_dt).days <= _DETACHED_MATCH_WINDOW_DAYS:
            score += 1
        subject_match = bool(norm_subject and event_subject and norm_subject == event_subject)
        recent_match = bool(
            event_dt and (now - event_dt).days <= _DETACHED_MATCH_WINDOW_DAYS
        )
        is_outbound_event = str(ev.get("event_type") or "").startswith("outbound_")
        if score > best_detached_score:
            best_detached_score = score
            detached_hit = (
                identity_id,
                campaign_id,
                "detached",
                "heuristic",
                canonical_thread_id or msg.thread_id or None,
            )
        candidate_key = (identity_id, campaign_id)
        current = detached_candidates.get(candidate_key)
        if current is None or score > int(current.get("score", -1)):
            detached_candidates[candidate_key] = {
                "score": score,
                "campaign_id": campaign_id,
                "canonical_thread_id": canonical_thread_id,
                "subject_match": subject_match,
                "recent_match": recent_match,
                "is_outbound_event": is_outbound_event,
            }

    hit = strict_hit or weak_hit
    if hit is None and best_detached_score >= 3 and detached_hit is not None:
        tied = [
            key
            for key, cand in detached_candidates.items()
            if int(cand.get("score", -1)) >= best_detached_score
        ]
        if len(tied) == 1:
            hit = detached_hit
    # Safer soft fallback: only accept detached matching when exactly one
    # (identity, campaign) candidate exists and it has recent outbound + same
    # subject evidence. Otherwise keep unmatched so downstream can queue manual
    # triage instead of silently attaching to the wrong campaign.
    if hit is None and len(detached_candidates) == 1:
        (only_identity_id, only_campaign_id), candidate = next(iter(detached_candidates.items()))
        if (
            int(candidate.get("score", -1)) >= 3
            and bool(candidate.get("subject_match"))
            and bool(candidate.get("recent_match"))
            and bool(candidate.get("is_outbound_event"))
        ):
            canonical_thread_id = candidate.get("canonical_thread_id")
            hit = (
                only_identity_id,
                only_campaign_id,
                "detached",
                "heuristic_unique_sender",
                str(canonical_thread_id) if canonical_thread_id else (msg.thread_id or None),
            )
    # region agent log
    _agent_debug_log(
        hypothesis_id="H2-H3-H4",
        location="kol_reply_dispatcher.py:_match_identity",
        message="identity_match_result",
        data={
            "msg_id": msg.message_id,
            "thread_id": msg.thread_id,
            "in_reply_to": (msg.in_reply_to or "")[:120],
            "sender": sender_email,
            "subject_norm": norm_subject[:120],
            "event_count": len(events_list),
            "strict_hit": strict_hit is not None,
            "weak_hit": weak_hit[:2] if weak_hit else None,
            "best_detached_score": best_detached_score,
            "detached_candidates_n": len(detached_candidates),
            "hit": hit[:3] if hit else None,
        },
    )
    # endregion
    if hit is None:
        return None

    identity_id, campaign_id, thread_integrity, matched_by, history_thread_id = hit
    expected_email = _expected_identity_email(identity_id)
    content_risk, controls = _derive_content_risk(msg)
    identity_integrity, reasons = _classify_identity_integrity(
        sender_email=sender_email,
        expected_email=expected_email,
        from_header=msg.from_addr,
        body=msg.body,
    )
    allow_autoflow, controls = resolve_autoflow_controls(
        content_risk=content_risk,
        thread_integrity=thread_integrity,
        identity_integrity=identity_integrity,
        controls=controls,
    )
    risk_controls = {"allow_autoflow": allow_autoflow, **controls}
    if thread_integrity == "detached":
        reasons.append("detached_thread_heuristic_match")
    return IdentityMatch(
        identity_id=identity_id,
        campaign_id=campaign_id,
        thread_integrity=thread_integrity,
        matched_by=matched_by,
        history_thread_id=history_thread_id,
        identity_integrity=identity_integrity,
        reasons=sorted(set(reasons)),
        content_risk=content_risk,
        risk_controls=risk_controls,
        sender_email=sender_email,
        expected_email=expected_email,
    )


# ---------------------------------------------------------------- main loop
def _dispatcher_instructions() -> str:
    """Reuse production gateway brief (handoff block + conversation_summary)."""
    try:
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "_gateway_client_for_legacy",
            root / "inbound_reply" / "gateway_client.py",
        )
        if spec is None or spec.loader is None:
            raise ImportError("gateway_client spec failed")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod.dispatcher_instructions()
    except Exception as exc:
        log.warning("fallback legacy dispatcher instructions: %s", exc)
        return (
            "You are running the `kol-reply-dispatcher` skill. Read the supplied "
            "pending_replies array and dispatch context, classify the inbound reply, "
            "persist classifier facts via the bridge CLI, then follow the skill's "
            "multi-goal flow. MANDATORY bridge CLI: "
            f"python3 {_BRIDGE_CLI_ABS} <subcommand> --env <env> ..."
        )


_DISPATCHER_INSTRUCTIONS = _dispatcher_instructions()


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


def _fetch_chase_context(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    message_id: str,
    thread_id: Optional[str],
    env: str,
) -> dict[str, Any]:
    if not campaign_id:
        return {"recommended_action": "proceed_normal", "prior_pending_draft": False}
    try:
        out = _BRIDGE.request(
            "GET",
            f"/identities/{identity_id}/reply-chase-hint",
            params={
                "campaign_id": campaign_id,
                "message_id": message_id,
                "thread_id": thread_id or "",
                "env": env,
            },
        )
    except SystemExit as exc:
        log.warning(
            "bridge reply-chase-hint failed identity=%s msg=%s: %s",
            identity_id,
            message_id,
            exc,
        )
        return {"recommended_action": "proceed_normal", "prior_pending_draft": False}
    return out if isinstance(out, dict) else {
        "recommended_action": "proceed_normal",
        "prior_pending_draft": False,
    }


_THREAD_MSG_BODY_CAP = 4000
_THREAD_HISTORY_TOTAL_CAP = 24000


def _build_thread_history(
    *,
    client: GmailClient,
    thread_id: str,
    latest_message_id: str,
) -> list[dict[str, str]]:
    """Return the prior-turn history for a Gmail thread, lean and ordered.

    Shape (oldest → newest, excludes the latest message which the dispatcher
    passes separately as ``latest_email``)::

        [{"from": "alice@x.com", "date": "Mon, 5 May 2026 ...", "body": "..."},
         ...]

    We strip every other field (id, headers, snippet, labels, thread_id,
    to/subject) — child drafting skills need the conversational text plus
    "who said when", nothing else. Each body is clipped, and the whole list
    is bounded to keep prompt budgets sane on long threads.
    """
    try:
        raw = client.get_thread(thread_id)
    except GmailUnavailable as exc:
        log.warning(
            "thread history unavailable thread=%s msg=%s: %s",
            thread_id,
            latest_message_id,
            exc,
        )
        return []
    # raw includes latest_message_id, but history/drop counts should not.
    prior_message_count = sum(1 for item in raw if item.get("id") != latest_message_id)
    history: list[dict[str, str]] = []
    total = 0
    for item in raw:
        if item.get("id") == latest_message_id:
            continue
        body = _clip_text(item.get("body", ""), _THREAD_MSG_BODY_CAP)
        entry = {
            "from": item.get("from", ""),
            "date": item.get("date", ""),
            "body": body,
        }
        total += len(body)
        if total > _THREAD_HISTORY_TOTAL_CAP and history:
            dropped_count = max(prior_message_count - len(history), 0)
            history.append({
                "from": "",
                "date": "",
                "body": f"... [history truncated: dropped {dropped_count} earlier message(s)]",
            })
            break
        history.append(entry)
    return history


def _pending_reply_payload(
    *,
    client: GmailClient,
    msg: GmailMessage,
    matched: IdentityMatch,
    env: str,
    mailbox_user_id: int = 0,
    mailbox_email: str = "",
) -> dict[str, Any]:
    identity_id = matched.identity_id
    campaign_id = matched.campaign_id
    context = _dispatch_context(identity_id, campaign_id, env)
    thread_history = _build_thread_history(
        client=client,
        thread_id=matched.history_thread_id or msg.thread_id,
        latest_message_id=msg.message_id,
    )
    chase_context = _fetch_chase_context(
        identity_id=identity_id,
        campaign_id=campaign_id,
        message_id=msg.message_id,
        thread_id=msg.thread_id,
        env=env,
    )
    return {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "latest_email": {
            "message_id": msg.message_id,
            "thread_id": msg.thread_id,
            "from": msg.from_addr,
            "to": msg.to,
            "cc": msg.cc,
            "subject": msg.subject,
            "date": msg.date,
            "in_reply_to": msg.in_reply_to,
            "references": msg.references,
            "snippet": msg.snippet,
            "body": _clip_text(msg.body),
        },
        "thread_history": thread_history,
        "detected_mailbox_user_id": mailbox_user_id or None,
        "detected_mailbox_email": mailbox_email or None,
        "anomaly_signals": {
            "thread_integrity": {
                "status": matched.thread_integrity,
                "matched_by": matched.matched_by,
                "history_thread_id": matched.history_thread_id or msg.thread_id,
            },
            "identity_integrity": {
                "status": matched.identity_integrity,
                "sender_email": matched.sender_email,
                "expected_email": matched.expected_email,
                "reasons": matched.reasons,
            },
            "content_risk": matched.content_risk,
            "risk_controls": matched.risk_controls,
            **_mailbox_mismatch_signal(
                identity_id=identity_id,
                campaign_id=campaign_id,
                env=env,
                detected_mailbox_email=mailbox_email,
            ),
        },
        "dispatch_context": context,
        "chase_context": chase_context,
    }


ProcessStatus = Literal["dispatched", "skipped", "retry"]
MailboxMismatchOutcome = Literal["none", "skip", "retry"]


def _mailbox_mismatch_signal(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    detected_mailbox_email: str,
) -> dict[str, Any]:
    if not campaign_id or not detected_mailbox_email:
        return {}
    try:
        facts = _BRIDGE.request(
            "GET",
            f"/facts/{identity_id}",
            params={"campaign_id": campaign_id, "env": env},
        )
    except SystemExit:
        return {}
    if not isinstance(facts, dict):
        return {}
    inner = facts.get("facts")
    if isinstance(inner, dict):
        facts = inner
    bound = str(facts.get("offer.gmail_mailbox_email") or "").strip().lower()
    if not bound or bound == detected_mailbox_email.lower():
        return {}
    return {
        "mailbox_mismatch": True,
        "bound_mailbox_email": bound,
        "detected_mailbox_email": detected_mailbox_email.lower(),
        "allow_autoflow": False,
    }


def _handle_mailbox_mismatch(
    *,
    identity_id: int,
    campaign_id: Optional[str],
    env: str,
    msg: GmailMessage,
    mailbox_email: str,
    mismatch: dict[str, Any],
) -> MailboxMismatchOutcome:
    """Open deterministic escalation and block auto-dispatch when handled."""
    if not mismatch.get("mailbox_mismatch") or not campaign_id:
        return "none"
    bound = str(mismatch.get("bound_mailbox_email") or "")
    detected = str(mismatch.get("detected_mailbox_email") or mailbox_email or "")
    if not bound or not detected:
        return "none"
    try:
        esc_id = ensure_mailbox_mismatch_escalation(
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            bound_mailbox_email=bound,
            detected_mailbox_email=detected,
        )
        log.warning(
            "[mailbox_mismatch] msg=%s identity=%s bound=%s detected=%s escalation=%s",
            msg.message_id,
            identity_id,
            bound,
            detected,
            esc_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("mailbox mismatch escalation failed msg=%s: %s", msg.message_id, exc)
        return "retry"
    return "skip"


def _process_message(
    msg: GmailMessage,
    env: str,
    *,
    client: GmailClient,
    mailbox_user_id: int = 0,
    mailbox_email: str = "",
) -> ProcessStatus:
    """Return whether the message was dispatched, skipped, or should retry."""
    try:
        matched = _match_identity(msg, env=env)
    except _MatchBridgeError as exc:
        log.error("[retry] msg=%s identity match bridge error: %s", msg.message_id, exc)
        return "retry"
    if not matched:
        # region agent log
        _agent_debug_log(
            hypothesis_id="H4",
            location="kol_reply_dispatcher.py:_process_message",
            message="skip_no_identity_match",
            data={
                "msg_id": msg.message_id,
                "from_domain": (_extract_email(msg.from_addr) or "").split("@")[-1],
                "thread_id": msg.thread_id,
            },
        )
        # endregion
        log.info("[skip] msg=%s no identity match (from=%s)", msg.message_id, msg.from_addr)
        return "skipped"
    identity_id = matched.identity_id
    campaign_id = matched.campaign_id

    try:
        dispatch_status = _BRIDGE.request(
            "GET",
            f"/identities/{identity_id}/reply-dispatch-status",
            params={
                "campaign_id": campaign_id,
                "message_id": msg.message_id,
                "env": env,
            },
        )
    except SystemExit:
        log.error("[retry] reply_dispatch_status failed msg=%s", msg.message_id)
        return "retry"
    if isinstance(dispatch_status, dict) and dispatch_status.get("should_skip_poller"):
        log.info(
            "[skip] msg=%s identity=%s already has reply draft (poller idempotency)",
            msg.message_id,
            identity_id,
        )
        return "skipped"
    retry_gateway_only = bool(
        isinstance(dispatch_status, dict)
        and dispatch_status.get("should_retry_gateway_only")
    )
    mismatch = _mailbox_mismatch_signal(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        detected_mailbox_email=mailbox_email,
    )

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
            "to": msg.to,
            "cc": msg.cc,
            "subject": msg.subject,
            "snippet": msg.snippet,
            # Persist the (clipped) body so the operator console can render
            # the actual email that triggered downstream escalations /
            # reply_draft approvals — snippet alone is too lossy for that
            # diagnostic surface.
            "body": _clip_text(msg.body, 8000),
            "date": msg.date,
            "detected_mailbox_user_id": mailbox_user_id or None,
            "detected_mailbox_email": mailbox_email or None,
            "anomaly_signals": {
                "thread_integrity": {
                    "status": matched.thread_integrity,
                    "matched_by": matched.matched_by,
                    "history_thread_id": matched.history_thread_id or msg.thread_id,
                },
                "identity_integrity": {
                    "status": matched.identity_integrity,
                    "sender_email": matched.sender_email,
                    "expected_email": matched.expected_email,
                    "reasons": matched.reasons,
                },
                "content_risk": matched.content_risk,
                "risk_controls": {
                    **matched.risk_controls,
                    **({"allow_autoflow": False} if mismatch.get("mailbox_mismatch") else {}),
                },
                **mismatch,
            },
        },
    }
    if not retry_gateway_only:
        try:
            _BRIDGE.request("POST", "/events", body=event_body)
        except SystemExit as exc:
            log.error("bridge POST /events failed for msg=%s: %s", msg.message_id, exc)
            return "retry"
    else:
        log.info(
            "[retry-gateway] msg=%s identity=%s inbound event exists, no draft yet",
            msg.message_id,
            identity_id,
        )

    mismatch_outcome = _handle_mailbox_mismatch(
        identity_id=identity_id,
        campaign_id=campaign_id,
        env=env,
        msg=msg,
        mailbox_email=mailbox_email,
        mismatch=mismatch,
    )
    if mismatch_outcome == "skip":
        return "skipped"
    if mismatch_outcome == "retry":
        return "retry"

    session_id = f"kol-reply:{env}:{identity_id}:{msg.message_id}"
    if retry_gateway_only:
        # Prior runs on the canonical session_id leave a bloated state.db
        # transcript (failed terminal loops). Use a fresh session so the
        # recovery run starts clean instead of replaying ~70k tokens of noise.
        session_id = f"{session_id}:retry-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d%H%M%S}"
    try:
        input_text = json.dumps({
            "pending_replies": [
                _pending_reply_payload(
                    client=client,
                    msg=msg,
                    matched=matched,
                    env=env,
                    mailbox_user_id=mailbox_user_id,
                    mailbox_email=mailbox_email,
                )
            ],
        }, indent=2, ensure_ascii=False)
    except GmailUnavailable as exc:
        log.error("[retry] pending_reply_payload failed msg=%s: %s", msg.message_id, exc)
        return "retry"
    run_id = _gateway_run(
        instructions=_DISPATCHER_INSTRUCTIONS,
        input_text=input_text,
        session_id=session_id,
    )
    if not run_id:
        log.error(
            "[retry] gateway dispatch did not return run_id for msg=%s — inbound event"
            " written; will retry via should_retry_gateway_only",
            msg.message_id,
        )
        return "retry"
    _register_console_run(
        campaign_id=campaign_id,
        env=env,
        run_id=run_id,
        session_id=session_id,
    )
    log.info(
        "dispatched msg=%s identity=%s campaign=%s run_id=%s thread=%s identity=%s risk=%s",
        msg.message_id,
        identity_id,
        campaign_id,
        run_id,
        matched.thread_integrity,
        matched.identity_integrity,
        matched.content_risk,
    )
    # region agent log
    _agent_debug_log(
        hypothesis_id="H1",
        location="kol_reply_dispatcher.py:_process_message",
        message="dispatched",
        data={
            "msg_id": msg.message_id,
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "matched_by": matched.matched_by,
        },
    )
    # endregion
    return "dispatched"


@contextlib.contextmanager
def _state_lock(*, blocking: bool = True) -> Iterator[None]:
    """Exclusive lock around a full run_once cycle.

    Without this, a watcher tick and a manually-invoked one-shot dispatcher
    can both read ``seen_LIVE``, both miss a message, both POST /events and
    /v1/runs for it, producing duplicate ``kol_inbound_reply`` rows and
    duplicate agent runs in the same second (see 2026-05-28 Megan incident:
    cal.db ids 4436/4437 landed at 02:06:49Z). Held for the whole cycle —
    read + process + write — so the second caller waits, then re-reads the
    updated ``seen`` and skips correctly. Lock is advisory (fcntl.flock),
    only honored by other dispatchers; manual edits of poller_state.json
    are still unsafe by design.
    """
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(_LOCK_PATH, "a+")
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError(
                    "another kol_reply_dispatcher run is in progress "
                    f"(lock={_LOCK_PATH})"
                ) from exc
            raise
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def run_once(*, env: str, lookback_days: int, max_results: int) -> dict[str, int]:
    mailboxes = list_operator_gmail_clients()
    if not mailboxes:
        raise GmailUnavailable("Gmail token / google_api.py unavailable")

    # region agent log
    _agent_debug_log(
        hypothesis_id="H1-H5",
        location="kol_reply_dispatcher.py:run_once",
        message="tick_start",
        data={
            "env": env,
            "mailbox_count": len(mailboxes),
            "lookback_days": lookback_days,
            "max_results": max_results,
        },
    )
    # endregion

    with _state_lock():
        state = _load_state()
        matched = 0
        skipped = 0
        retry = 0
        errors = 0
        deferred = 0
        scanned = 0
        query = f"in:inbox newer_than:{int(lookback_days)}d -from:me"
        for mb in mailboxes:
            seen_key = f"seen_{env}_{mb.user_id}"
            seen: set[str] = set(state.get(seen_key, []))
            messages = mb.client.search(query=query, max_results=max_results)
            scanned += len(messages)
            for stub in messages:
                if stub.message_id in seen:
                    continue
                if _retry_not_before(state, env=env, message_id=stub.message_id) > time.time():
                    deferred += 1
                    continue
                globally_seen = _global_message_seen(env=env, message_id=stub.message_id)
                if globally_seen:
                    seen.add(stub.message_id)
                try:
                    full = mb.client.get_message(stub.message_id)
                except GmailUnavailable as exc:
                    log.warning("gmail get %s failed: %s", stub.message_id, exc)
                    retry += 1
                    continue
                if globally_seen and not _needs_reprocess_after_global_seen(full, env=env):
                    continue
                try:
                    status = _process_message(
                        full,
                        env=env,
                        client=mb.client,
                        mailbox_user_id=mb.user_id,
                        mailbox_email=mb.google_email,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "process_message crashed msg=%s: %s",
                        stub.message_id,
                        exc,
                    )
                    errors += 1
                    retry += 1
                    _record_retry_backoff(state, env=env, message_id=full.message_id)
                    continue
                if status == "retry":
                    _record_retry_backoff(state, env=env, message_id=full.message_id)
                    state[f"last_run_{env}"] = int(time.time())
                    _save_state(state)
                    retry += 1
                    continue
                if status in ("dispatched", "skipped"):
                    _clear_retry_backoff(state, env=env, message_id=full.message_id)
                    seen.add(full.message_id)
                    _record_global_message_seen(
                        env=env,
                        message_id=full.message_id,
                        mailbox_user_id=mb.user_id,
                    )
                    state[seen_key] = sorted(seen)[-2000:]
                    state[f"last_run_{env}"] = int(time.time())
                    _save_state(state)
                if status == "dispatched":
                    matched += 1
                elif status == "skipped":
                    skipped += 1
            state[seen_key] = sorted(seen)[-2000:]
        state[f"last_run_{env}"] = int(time.time())
        _save_state(state)
        return {
            "matched": matched,
            "skipped": skipped,
            "retry": retry,
            "errors": errors,
            "deferred": deferred,
            "scanned": scanned,
            "mailboxes": len(mailboxes),
        }


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
