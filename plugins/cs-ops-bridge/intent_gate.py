"""Gate inbound processing on QuickCEP AI intention tags (intentionTags).

When ``CS_INTENT_ENABLED=true`` the gate delegates to the standalone
``cs-intent-classifier`` service (see plugins/cs-intent-classifier/). The seam
pre-fetches the full email body + dispatch-context (orders + shipping address),
calls ``POST /classify`` on the classifier, and gates on the returned
``in_scope``. If the classifier is unreachable, the seam falls through to the
existing QuickCEP ``intentionTags`` logic so inbound is never blocked by a
classifier outage (graceful degradation). When the switch is ``false``
(default), behavior is identical to today — zero regression.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

_DEBUG_LOG_PATH = Path("/Users/arnold/agent_prj/.cursor/debug-400546.log")
_DEBUG_SESSION_ID = "400546"


def _agent_debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": _DEBUG_SESSION_ID,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "intent_filter.yaml"


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {"enabled": True, "allowed_intention_tags": ["产品咨询", "物流咨询"]}
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def intent_filter_enabled() -> bool:
    env = os.environ.get("CS_OPS_INTENT_FILTER")
    if env is not None:
        return _truthy(env, default=True)
    return bool(_load_config().get("enabled", True))


def allowed_intention_tags() -> frozenset[str]:
    env = os.environ.get("CS_OPS_ALLOWED_INTENTION_TAGS")
    if env is not None:
        tags = [t.strip() for t in env.split(",") if t.strip()]
        return frozenset(tags)
    raw = _load_config().get("allowed_intention_tags") or ["产品咨询", "物流咨询"]
    return frozenset(str(t).strip() for t in raw if str(t).strip())


@dataclass(frozen=True)
class IntentGateResult:
    allowed: bool
    reason: str
    tags: tuple[str, ...]


def normalize_intention_tags(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def matches_allowed_intention(tags: tuple[str, ...]) -> bool:
    if not tags:
        return False
    allowed = allowed_intention_tags()
    return any(tag in allowed for tag in tags)


def fetch_session_intention_tags(session_id: str) -> tuple[str, ...]:
    """Load intentionTags for one email session via QuickCEP session list API."""
    from .email_channel import fetch_email_session_row

    row = fetch_email_session_row(session_id)
    if not row:
        return ()
    return normalize_intention_tags(row.get("intentionTags"))


def check_intent_gate(
    session_id: str,
    intention_tags: Any = None,
    *,
    fetch_if_missing: bool = True,
    customer_email: str | None = None,
    env: str = "LIVE",
    info: Optional[dict[str, Any]] = None,
    force_reclassify: bool = False,
) -> IntentGateResult:
    """Return whether watcher should launch automation for this session.

    When ``CS_INTENT_ENABLED=true``, delegates to the cs-intent-classifier
    service (pre-fetches body + dispatch-context, calls POST /classify, gates
    on ``in_scope``). Falls through to the legacy QuickCEP-tag logic when the
    classifier is unreachable or the switch is off.

    When ``force_reclassify=True``, skips the classifier cache and re-runs
    POST /classify. Used when a session is reopened by a new customer message
    to detect intent changes (e.g. logistics_inquiry → order_management).
    """
    if _cs_intent_enabled():
        result = _classifier_gate(
            session_id=session_id,
            env=env,
            customer_email=customer_email,
            info=info,
            force_reclassify=force_reclassify,
        )
        if result is not None:
            return result
        # classifier unreachable → graceful fallthrough to legacy logic
        log.warning(
            "cs-intent-classifier unavailable — falling back to QuickCEP intentionTags gate for session %s",
            session_id,
        )

    if not intent_filter_enabled():
        return IntentGateResult(True, "filter_disabled", ())

    tags = normalize_intention_tags(intention_tags)
    if not tags and fetch_if_missing and session_id:
        tags = fetch_session_intention_tags(session_id)

    if not tags:
        from . import cal

        if customer_email and cal.has_prior_session_for_email(
            customer_email=customer_email,
            env=env,
            exclude_quickcep_session_id=session_id or None,
        ):
            _agent_debug_log(
                hypothesis_id="F",
                location="intent_gate.py:check_intent_gate",
                message="prior customer bypass without intention tags",
                data={
                    "quickcep_session_id": session_id,
                    "customer_email_domain": customer_email.split("@")[-1] if "@" in customer_email else "",
                },
            )
            return IntentGateResult(True, "prior_customer_no_intent_tags", ())
        _agent_debug_log(
            hypothesis_id="F",
            location="intent_gate.py:check_intent_gate",
            message="blocked no intention tags",
            data={"quickcep_session_id": session_id, "has_customer_email": bool(customer_email)},
        )
        return IntentGateResult(False, "no_intention_tags", ())

    if matches_allowed_intention(tags):
        return IntentGateResult(True, "allowed", tags)

    allowed = ", ".join(sorted(allowed_intention_tags()))
    return IntentGateResult(False, f"intention_not_allowed (allowed: {allowed})", tags)


# ── cs-intent-classifier seam (switch-controlled, graceful degradation) ──


def _cs_intent_enabled() -> bool:
    """Read the CS_INTENT_ENABLED switch. Default false (zero regression)."""
    return _truthy(os.environ.get("CS_INTENT_ENABLED"), default=False)


# Thread pool for the async seam — caps SIO/REST callback blocking at
# CS_INTENT_SEAM_TIMEOUT so a slow classifier can't stall the watcher's
# single-threaded event loops. Two workers handle the rare overlap of an
# SIO event arriving during a REST reconcile classify.
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout

_SEAM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cs-intent-seam")


def _seam_timeout() -> float:
    """Hard cap for the whole classify seam (pre-fetch + POST). Default 45s
    (30s LLM + ~10s pre-fetch buffer); tune down for faster endpoints."""
    try:
        return float(os.environ.get("CS_INTENT_SEAM_TIMEOUT", "45"))
    except ValueError:
        return 45.0


def _classifier_gate(
    *,
    session_id: str,
    env: str,
    customer_email: str | None,
    info: Optional[dict[str, Any]],
    force_reclassify: bool = False,
) -> Optional[IntentGateResult]:
    """Call the cs-intent-classifier service. Returns IntentGateResult or None on failure.

    Pre-fetches full email body + dispatch-context (orders + shipping addresses),
    assembles the classify request metadata, and POSTs to the classifier.
    Returns None when the classifier is unreachable (or the seam exceeds
    ``CS_INTENT_SEAM_TIMEOUT``) so the caller falls back to the legacy
    QuickCEP-tag gate. The blocking work runs in ``_SEAM_EXECUTOR`` so the
    watcher's SIO/REST thread is never blocked longer than the timeout.
    """
    future = _SEAM_EXECUTOR.submit(
        _classifier_gate_work,
        session_id=session_id,
        env=env,
        customer_email=customer_email,
        info=info,
        force_reclassify=force_reclassify,
    )
    try:
        return future.result(timeout=_seam_timeout())
    except _FutureTimeout:
        log.warning(
            "cs-intent-classifier seam timed out after %.1fs for session %s — falling back to QuickCEP gate",
            _seam_timeout(),
            session_id,
        )
        future.cancel()
        return None
    except Exception as exc:
        log.warning("cs-intent-classifier seam failed for session %s: %s", session_id, exc)
        return None


def _gate_result_from_ge(ge: dict[str, Any]) -> IntentGateResult:
    """Build an IntentGateResult from a gate_extract dict (shared by cache + POST paths)."""
    in_scope = bool(ge.get("in_scope"))
    primary = ge.get("primary_intent") or "unknown"
    if in_scope:
        reason = f"classifier:{primary}:in_scope"
    else:
        # Prefix with intention_not_allowed so the watcher's existing permanent-skip
        # check (gate.reason.startswith("intention_not_allowed")) enqueues it into CAL
        # with status=skipped — matching the legacy out-of-allowlist behavior. Without
        # this the out_of_scope result would be transient (log-only) and retried every
        # REST tick, wasting classifier calls and never recording the skip.
        reason = f"intention_not_allowed (classifier:{primary}:out_of_scope)"
    return IntentGateResult(in_scope, reason, ())


def _fetch_cached_gate_extract(*, session_id: str, env: str) -> Optional[dict[str, Any]]:
    """GET /gate-extract/{id} on the classifier. Returns cached gate_extract or None.

    None when: never classified (404), classifier unreachable, or any error.
    This is the idempotency check that prevents redundant LLM calls when a prior
    POST /classify succeeded server-side but the client timed out before reading
    the response (the classic "urlopen timed out but FastAPI kept running" race).
    """
    import urllib.error
    import urllib.request

    base = os.environ.get("CS_INTENT_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
    url = f"{base}/gate-extract/{session_id}?env={env}"
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # never classified — proceed to POST
        log.debug("cache GET /gate-extract HTTP %s for session %s", exc.code, session_id)
        return None
    except Exception as exc:
        log.debug("cache GET /gate-extract failed for session %s: %s", session_id, exc)
        return None


def _classifier_gate_work(
    *,
    session_id: str,
    env: str,
    customer_email: str | None,
    info: Optional[dict[str, Any]],
    force_reclassify: bool = False,
) -> Optional[IntentGateResult]:
    """Blocking body of the classifier seam (runs inside _SEAM_EXECUTOR)."""
    import urllib.error
    import urllib.request

    # ── Idempotency cache ──────────────────────────────────────────
    # If the classifier already has a result for this session (e.g. a prior
    # call succeeded server-side but the HTTP client timed out reading the
    # response), reuse it instead of re-running the expensive LLM. Without
    # this, a slow LLM endpoint causes unbounded retries: the client times
    # out → graceful fallback → transient skip (no CAL dedup) → next REST
    # tick re-POSTs → server runs LLM again → wasted call. The cache short
    # -circuits this loop after the first successful server-side classify.
    if not force_reclassify:
        try:
            cached = _fetch_cached_gate_extract(session_id=session_id, env=env)
        except Exception as exc:
            log.debug("cache check crashed for session %s: %s", session_id, exc)
            cached = None
        if cached is not None:
            log.info("cs-intent-classifier cache hit for session %s — skipping POST /classify", session_id)
            return _gate_result_from_ge(cached)
    else:
        log.info("force_reclassify for session %s — bypassing classifier cache", session_id)

    subject = (info or {}).get("email_subject") or ""
    visitor_info = (info or {}).get("visitorInfo") or {}
    # QuickCEP visitorInfo has no `.geo` sub-dict; fall back to the `country`
    # locale field as a medium-confidence region signal.
    visitor_geo = visitor_info.get("geo") if isinstance(visitor_info, dict) else None
    if not visitor_geo and isinstance(visitor_info, dict) and visitor_info.get("country"):
        visitor_geo = {"country": visitor_info.get("country"), "province_state": None}

    # Pre-fetch full body + dispatch-context (orders + addresses) + conversation history.
    body, order_addresses, conversation_history = _prefetch_body_and_orders(
        session_id=session_id, env=env
    )
    if body is None:
        # Pre-fetch failed → can't classify → fall through
        return None

    metadata: dict[str, Any] = {
        "customer_email": customer_email or "",
        "intention_tags": list(normalize_intention_tags((info or {}).get("intentionTags"))),
        "visitor_geo": visitor_geo or {},
        "order_addresses": order_addresses,
    }
    # prior session info for conversation_stage/customer_segment
    try:
        from . import cal

        has_prior = cal.has_prior_session_for_email(
            customer_email=customer_email or "",
            env=env,
            exclude_quickcep_session_id=session_id or None,
        ) if customer_email else False
        metadata["has_prior_session"] = bool(has_prior)
    except Exception:
        metadata["has_prior_session"] = False

    payload = json.dumps(
        {
            "session_id": session_id,
            "env": env,
            "subject": subject,
            "body": body,
            "metadata": metadata,
            "conversation_history": conversation_history,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    base = os.environ.get("CS_INTENT_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
    req = urllib.request.Request(
        base + "/classify",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # urlopen timeout must be >= the LLM timeout (CS_INTENT_LLM_TIMEOUT, default
    # 30s) so the client doesn't give up before the server finishes. A 3s timeout
    # here caused unbounded duplicate LLM calls: the client aborted at 3s, the
    # FastAPI server kept running the LLM to completion (writing to cs_intent.db),
    # the watcher fell back to "no_intention_tags" (transient skip, no CAL dedup),
    # and the next REST tick re-POSTed — repeating the waste. Aligned to
    # _seam_timeout() (default 45s) which is also the future.result cap.
    try:
        with urllib.request.urlopen(req, timeout=_seam_timeout()) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log.warning("cs-intent-classifier POST /classify HTTP %s for session %s", exc.code, session_id)
        return None
    except Exception as exc:
        log.warning("cs-intent-classifier unreachable for session %s: %s", session_id, exc)
        return None

    ge = data.get("gate_extract") or {}
    return _gate_result_from_ge(ge)


_CDATA_OPEN_RE = re.compile(r"<!\[CDATA\[")
_CDATA_CLOSE_RE = re.compile(r"\]\]>")
_WS_RUN_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _clean_extracted_text(text: str) -> str:
    """Clean text extracted from a QuickCEP message record.

    The bridge CLI's ``html_to_plain`` uses a simple ``<[^>]+>`` tag-strip. It
    removes the opening ``<![CDATA[`` (contains angle brackets) but leaves the
    closing ``]]>`` (no angle brackets), and it does not collapse the large
    runs of whitespace left behind by HTML table layouts. Both artifacts leak
    into the classifier prompt as noise / wasted tokens.

    This helper:
    - Strips CDATA section remnants (``<![CDATA[`` and ``]]>``)
    - Collapses runs of spaces/tabs to a single space
    - Collapses 3+ consecutive newlines to a single blank line
    - Trims leading/trailing whitespace

    Idempotent: safe to call on already-clean text.
    """
    if not text:
        return ""
    # CDATA remnants — opening tag (rare, defensive) and closing marker.
    text = _CDATA_OPEN_RE.sub("", text)
    text = _CDATA_CLOSE_RE.sub("", text)
    # Collapse whitespace runs left by HTML table layouts.
    text = _WS_RUN_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _extract_message_text(msg: dict[str, Any]) -> str:
    """Extract plain text body from a QuickCEP message record.

    QuickCEP html messages store `content` as a dict (parsed JSON) with the
    inner text under `content.content`; text messages store it as a string.
    The CLI's `--plain` (default) already html_to_plain'd the inner text, but
    that pass leaves CDATA remnants and uncollapsed whitespace — cleaned here.
    """
    content = msg.get("content")
    if isinstance(content, dict):
        # html contentType: {"content": "<plain text>", "subject": "...", ...}
        raw = str(content.get("content") or content.get("text") or "")
    elif isinstance(content, str):
        raw = content
    else:
        raw = str(msg.get("body") or msg.get("text") or "")
    return _clean_extracted_text(raw)


def _latest_visitor_message(messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick the newest customer (`ownerType=visitor`) message from a QuickCEP list.

    QuickCEP inserts system messages around the real email — `chat_start`,
    `ruleAssignHumanQueue`, `assignChat` — all with `ownerType=system` and
    JSON-action `content`. The CLI returns messages chronologically, so the
    last list entry is often a system assignment message, NOT the customer's
    email. Feeding that to the classifier made the LLM label real customer
    emails as "系统自动分配通知" → spam_irrelevant (out_of_scope).

    Only `ownerType=visitor` rows carry the actual customer text. Phone-call
    records (`contentType=call`) are excluded — they are metadata, not email
    bodies. We pick the latest visitor email; if none exists (brand-new session
    with only system rows, or operator-only thread) return None so the caller
    falls through to the legacy gate instead of classifying system noise.
    """
    visitor = [m for m in messages if _is_visitor_email_message(m)]
    return visitor[-1] if visitor else None


def _context_turns() -> int:
    """Number of recent messages to include as conversation context. Default 3.

    Set CS_INTENT_CONTEXT_TURNS=1 to disable history (only classify the last
    email). Override via env var.
    """
    try:
        return max(1, int(os.environ.get("CS_INTENT_CONTEXT_TURNS", "3")))
    except ValueError:
        return 3


# ownerType values that are NOT conversation content (system actions, internal
# notes, bot messages). These are filtered out when building conversation history.
_NON_CONVERSATION_TYPES = frozenset({"system", "botsystem", "bot", "operatornote"})
# contentType values that are NOT email conversation. Includes phone-call
# metadata and customer rating / survey events (invite_score / score_notify).
# Rating rows are excluded so the classifier never treats a CSAT payload
# (e.g. "Terrible communication") as the latest visitor email body, and so
# conversation history does not include rating telemetry as a turn.
_NON_CONVERSATION_CONTENT_TYPES = frozenset({"call", "invite_score", "score_notify"})


def _is_conversation_message(msg: dict[str, Any]) -> bool:
    """True when a QuickCEP row is real email conversation (visitor/operator)."""
    if not isinstance(msg, dict):
        return False
    if str(msg.get("ownerType") or "").lower() in _NON_CONVERSATION_TYPES:
        return False
    if str(msg.get("contentType") or "").lower() in _NON_CONVERSATION_CONTENT_TYPES:
        return False
    return True


def _is_visitor_email_message(msg: dict[str, Any]) -> bool:
    """True when a row is a customer email (visitor), not a phone-call record."""
    return (
        isinstance(msg, dict)
        and str(msg.get("ownerType") or "").lower() == "visitor"
        and str(msg.get("contentType") or "").lower() not in _NON_CONVERSATION_CONTENT_TYPES
    )


def _strip_quoted_reply(text: str) -> str:
    """Remove quoted reply content from an email body.

    Reply emails contain the previous message quoted below the new content.
    Patterns handled (validated against real QuickCEP email data):

    - Gmail reply: ``On [date] [name] <email> wrote:`` — cut everything after
    - Forwarded: ``---------- Forwarded message ---------`` — cut after
    - Outlook: ``-----Original Message-----`` — cut after
    - Forwarded header block: ``From:`` followed by ``Subject:``/``Date:``/``To:``
    - ``>`` prefixed lines (Outlook/Apple Mail style)
    - HTML entities (``&lt;`` ``&gt;`` ``&nbsp;``) are decoded first

    Returns the customer's actual new content with quotes stripped.
    """
    import html as _html

    text = _html.unescape(text)
    # Cut at the first quote marker — everything after is quoted/forwarded content.
    markers = [
        r"(?im)^\s*on\s.{5,200}?\swrote\s*:",  # Gmail: On [date] [name] wrote:
        r"(?im)-{5,}\s*forwarded message\s*-{5,}",  # Gmail forwarded
        r"(?im)-{2,}\s*original\s+message\s*-{2,}",  # Outlook original message
        r"(?im)^\s*from\s*:.*\n(.*\n){0,5}(subject|date|to)\s*:",  # Forwarded header
    ]
    for pat in markers:
        m = re.search(pat, text)
        if m:
            text = text[: m.start()].rstrip()
    # Remove > prefixed quote lines (Outlook/Apple Mail style).
    text = "\n".join(
        line for line in text.splitlines() if not re.match(r"^\s*>", line)
    )
    return text.strip()


def _extract_conversation_history(
    messages: list[dict[str, Any]], max_turns: int
) -> list[dict[str, str]]:
    """Extract recent conversation history before the latest visitor message.

    Returns a list of ``{role, text}`` dicts (oldest-first), excluding the
    latest visitor message (which is passed separately as ``body``). Filters
    out system/bot/internal-note/phone-call messages. Each message's text is
    cleaned via ``_strip_quoted_reply`` to remove quoted content. When fewer
    messages exist than ``max_turns - 1``, returns as many as available.
    """
    # Keep only visitor (customer) and operator (agent reply) email messages.
    conversation = [m for m in messages if _is_conversation_message(m)]
    if not conversation:
        return []

    # Find the index of the latest visitor email (the one being classified).
    last_visitor_idx = None
    for i in range(len(conversation) - 1, -1, -1):
        if _is_visitor_email_message(conversation[i]):
            last_visitor_idx = i
            break
    if last_visitor_idx is None or last_visitor_idx == 0:
        return []

    # History = everything before the latest visitor message, up to max_turns-1.
    history_msgs = conversation[:last_visitor_idx]
    take = max_turns - 1
    if take > 0:
        history_msgs = history_msgs[-take:]
    else:
        return []

    out: list[dict[str, str]] = []
    for m in history_msgs:
        ot = str(m.get("ownerType") or "").lower()
        role = "customer" if ot == "visitor" else "agent"
        text = _strip_quoted_reply(_extract_message_text(m))
        if text:
            out.append({"role": role, "text": text})
    return out


def _prefetch_body_and_orders(
    *, session_id: str, env: str
) -> tuple[Optional[str], list[dict[str, Any]], list[dict[str, str]]]:
    """Pre-fetch full latest email body + dispatch-context orders + conversation history.

    Returns (body, order_addresses, conversation_history). body is None on
    failure. Uses the bridge CLI subprocess with a timeout — keeps the
    classifier decoupled from QuickCEP internals (fetching stays in
    cs-ops-bridge's domain).
    """
    from .bridge_agent_contract import cs_bridge_cli_path

    cli = str(cs_bridge_cli_path())
    body: Optional[str] = None
    order_addresses: list[dict[str, Any]] = []
    conversation_history: list[dict[str, str]] = []

    # 1. get-messages → latest CUSTOMER email body + conversation history. The
    #    CLI returns messages chronologically, but the last row is frequently a
    #    system message (chat_start / ruleAssignHumanQueue / assignChat) inserted
    #    AFTER the customer email. Filter to ownerType=visitor so the classifier
    #    sees the real customer text, not the system assignment notice.
    try:
        out = subprocess.run(
            ["python3", cli, "get-messages", "--env", env, "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            messages = data.get("messages") or []
            if isinstance(messages, list) and messages:
                last = _latest_visitor_message(messages)
                if last is not None:
                    body = _extract_message_text(last)
                    conversation_history = _extract_conversation_history(
                        messages, _context_turns()
                    )
    except Exception as exc:
        log.warning("prefetch get-messages failed session=%s: %s", session_id, exc)
        return None, [], []

    # 2. get-dispatch-context → order IDs (the order objects from QuickCEP
    #    getOrderList do NOT carry shipping address; country is fetched
    #    separately from the Povison order-track API below).
    order_ids: list[str] = []
    try:
        out = subprocess.run(
            ["python3", cli, "get-dispatch-context", "--env", env, "--session-id", session_id],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            # dispatch-context returns {"orders": {"orders": [...], ...}, ...}
            # (the inner dict is _fetch_visitor_orders' return value).
            orders_field = data.get("orders") or {}
            orders_list = (
                orders_field.get("orders") if isinstance(orders_field, dict) else orders_field
            )
            if isinstance(orders_list, list):
                for order in orders_list:
                    if isinstance(order, dict):
                        oid = str(order.get("orderId") or order.get("id") or order.get("order_id") or "").strip()
                        if oid:
                            order_ids.append(oid)
    except Exception as exc:
        log.debug("prefetch get-dispatch-context failed session=%s: %s", session_id, exc)

    # 3. Povison order-track API → customer country per order (high-confidence
    #    region source). Reuses the order_tracking circuit breaker + retry.
    #    province_state is not available (order-track state/city are warehouse
    #    origin, not customer address).
    if order_ids:
        try:
            from . import order_tracking

            order_addresses = order_tracking.fetch_order_countries(order_ids)
        except Exception as exc:
            log.debug("order_tracking.fetch_order_countries failed session=%s: %s", session_id, exc)

    return body, order_addresses, conversation_history
