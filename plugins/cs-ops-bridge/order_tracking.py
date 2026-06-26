"""Best-effort Povison order tracking prefill for dispatch-context."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

log = logging.getLogger(__name__)

_TRACK_API_BASE = "https://us.povison.com/api/order-server/openApi/customer/order/track"
_STORE_ID = "3"
_REQUEST_TIMEOUT_S = 8.0
_MAX_ATTEMPTS = 2
_MAX_ORDERS = 3
_RETRY_BACKOFF_S = 0.3

# Lightweight circuit breaker: avoid hammering upstream on repeated failures.
_CB_FAILURE_THRESHOLD = 3
_CB_OPEN_SECONDS = 60.0
_cb_lock = threading.Lock()
_cb_consecutive_failures = 0
_cb_open_until = 0.0


def _cb_is_open(now: float | None = None) -> bool:
    cur = now if now is not None else time.time()
    with _cb_lock:
        return _cb_open_until > cur


def _cb_mark_success() -> None:
    global _cb_consecutive_failures, _cb_open_until
    with _cb_lock:
        _cb_consecutive_failures = 0
        _cb_open_until = 0.0


def _cb_mark_failure() -> None:
    global _cb_consecutive_failures, _cb_open_until
    with _cb_lock:
        _cb_consecutive_failures += 1
        if _cb_consecutive_failures >= _CB_FAILURE_THRESHOLD:
            _cb_open_until = time.time() + _CB_OPEN_SECONDS


def _coerce_order_ids(order_ids: Iterable[str]) -> list[str]:
    clean: list[str] = []
    for raw in order_ids:
        text = str(raw or "").strip()
        if text and text not in clean:
            clean.append(text)
    return clean


def _extract_latest_event(record: dict[str, Any]) -> dict[str, str]:
    events = record.get("tracking_log")
    if not isinstance(events, list) or not events:
        return {}
    head = events[0] if isinstance(events[0], dict) else {}
    desc = (
        head.get("content")
        or head.get("description")
        or head.get("statusDesc")
        or head.get("title")
        or ""
    )
    ts = (
        head.get("track_time")
        or head.get("create_time")
        or head.get("createDate")
        or head.get("time")
        or ""
    )
    out: dict[str, str] = {}
    if desc:
        out["description"] = str(desc)
    if ts:
        out["time"] = str(ts)
    return out


def _extract_summary(order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    records = info.get("records") if isinstance(info.get("records"), list) else []
    if not records:
        return {"orderId": order_id, "found": False}

    record = records[0] if isinstance(records[0], dict) else {}
    status = str(record.get("delivery_status") or record.get("status_code") or "").strip()
    summary: dict[str, Any] = {
        "orderId": order_id,
        "found": True,
        "status": status or "unknown",
        "trackingNumber": str(record.get("tracking_number") or record.get("pro_number") or "").strip(),
        "courier": str(record.get("courier_name") or "").strip(),
        "earliestEdd": str(record.get("earliest_edd") or "").strip(),
        "latestEdd": str(record.get("latest_edd") or "").strip(),
    }
    latest_event = _extract_latest_event(record)
    if latest_event:
        summary["latestEvent"] = latest_event
    return summary


def _fetch_one(order_id: str) -> tuple[dict[str, Any] | None, str | None]:
    q = urllib.parse.urlencode({"orderId": order_id})
    req = urllib.request.Request(
        f"{_TRACK_API_BASE}?{q}",
        headers={"storeid": _STORE_ID, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(data, dict):
            return None, "non_json_response"
        return data, None
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return None, f"url_error:{str(exc.reason)[:120]}"
    except TimeoutError:
        return None, "timeout"
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"unexpected:{type(exc).__name__}"


def fetch_tracking_prefill(order_ids: Iterable[str], *, max_orders: int = _MAX_ORDERS) -> dict[str, Any]:
    """Return compact tracking summaries for dispatch-context prefill."""
    ids = _coerce_order_ids(order_ids)[: max(1, int(max_orders))]
    if not ids:
        return {
            "enabled": False,
            "source": "order-track-api",
            "reason": "no_order_ids",
            "circuitOpen": False,
            "summaries": [],
            "errors": [],
        }

    if _cb_is_open():
        return {
            "enabled": False,
            "source": "order-track-api",
            "reason": "circuit_open",
            "circuitOpen": True,
            "summaries": [],
            "errors": ["circuit_open"],
        }

    summaries: list[dict[str, Any]] = []
    errors: list[str] = []

    for order_id in ids:
        last_err = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            payload, err = _fetch_one(order_id)
            if payload is not None:
                summaries.append(_extract_summary(order_id, payload))
                _cb_mark_success()
                last_err = None
                break
            last_err = err or "unknown_error"
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_S * attempt)
        if last_err:
            _cb_mark_failure()
            errors.append(f"{order_id}:{last_err}")
            log.warning("tracking prefill failed order=%s err=%s", order_id, last_err)

    enabled = bool(summaries)
    reason = "ok" if enabled else "all_failed"
    return {
        "enabled": enabled,
        "source": "order-track-api",
        "reason": reason,
        "circuitOpen": _cb_is_open(),
        "summaries": summaries,
        "errors": errors,
    }
