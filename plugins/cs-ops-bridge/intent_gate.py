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
) -> IntentGateResult:
    """Return whether watcher should launch automation for this session.

    When ``CS_INTENT_ENABLED=true``, delegates to the cs-intent-classifier
    service (pre-fetches body + dispatch-context, calls POST /classify, gates
    on ``in_scope``). Falls through to the legacy QuickCEP-tag logic when the
    classifier is unreachable or the switch is off.
    """
    if _cs_intent_enabled():
        result = _classifier_gate(
            session_id=session_id,
            env=env,
            customer_email=customer_email,
            info=info,
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


def _classifier_gate_work(
    *,
    session_id: str,
    env: str,
    customer_email: str | None,
    info: Optional[dict[str, Any]],
) -> Optional[IntentGateResult]:
    """Blocking body of the classifier seam (runs inside _SEAM_EXECUTOR)."""
    import urllib.error
    import urllib.request

    subject = (info or {}).get("email_subject") or ""
    visitor_info = (info or {}).get("visitorInfo") or {}
    # QuickCEP visitorInfo has no `.geo` sub-dict; fall back to the `country`
    # locale field as a medium-confidence region signal.
    visitor_geo = visitor_info.get("geo") if isinstance(visitor_info, dict) else None
    if not visitor_geo and isinstance(visitor_info, dict) and visitor_info.get("country"):
        visitor_geo = {"country": visitor_info.get("country"), "province_state": None}

    # Pre-fetch full body + dispatch-context (orders + addresses).
    body, order_addresses = _prefetch_body_and_orders(session_id=session_id, env=env)
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
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log.warning("cs-intent-classifier POST /classify HTTP %s for session %s", exc.code, session_id)
        return None
    except Exception as exc:
        log.warning("cs-intent-classifier unreachable for session %s: %s", session_id, exc)
        return None

    ge = data.get("gate_extract") or {}
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


def _extract_message_text(msg: dict[str, Any]) -> str:
    """Extract plain text body from a QuickCEP message record.

    QuickCEP html messages store `content` as a dict (parsed JSON) with the
    inner text under `content.content`; text messages store it as a string.
    The CLI's `--plain` (default) already html_to_plain'd the inner text.
    """
    content = msg.get("content")
    if isinstance(content, dict):
        # html contentType: {"content": "<plain text>", "subject": "...", ...}
        return str(content.get("content") or content.get("text") or "")
    if isinstance(content, str):
        return content
    return str(msg.get("body") or msg.get("text") or "")


def _prefetch_body_and_orders(*, session_id: str, env: str) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Pre-fetch full latest email body + dispatch-context orders.

    Returns (body, order_addresses). body is None on failure. Uses the bridge
    CLI subprocess with a timeout — keeps the classifier decoupled from
    QuickCEP internals (fetching stays in cs-ops-bridge's domain).
    """
    from .bridge_agent_contract import cs_bridge_cli_path

    cli = str(cs_bridge_cli_path())
    body: Optional[str] = None
    order_addresses: list[dict[str, Any]] = []

    # 1. get-messages → latest email body (chronological order is the CLI default,
    #    so messages[-1] is the newest inbound).
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
            if messages:
                last = messages[-1] if isinstance(messages, list) else messages
                body = _extract_message_text(last)
    except Exception as exc:
        log.warning("prefetch get-messages failed session=%s: %s", session_id, exc)
        return None, []

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

    return body, order_addresses
