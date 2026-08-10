"""HTTP client for SERP API calls — retry + circuit breaker.

Uses ``urllib`` (stdlib, no extra deps) so the plugin works in the gateway
venv without installing anything. Retry with exponential backoff on 429/5xx.
A simple circuit breaker trips after N consecutive failures to avoid
hammering a down/quota-exhausted API.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Circuit-breaker state (process-wide; best-effort).
_fail_streak = 0
_open_until = 0.0


def _timeout() -> int:
    return int(os.environ.get("SERP_API_TIMEOUT", "15"))


def _max_retries() -> int:
    return int(os.environ.get("SERP_API_MAX_RETRIES", "3"))


def _breaker_threshold() -> int:
    return int(os.environ.get("SERP_API_BREAKER_THRESHOLD", "5"))


def _breaker_reset_s() -> int:
    return int(os.environ.get("SERP_API_BREAKER_RESET_S", "60"))


def _is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _check_breaker(provider: str) -> str | None:
    """Return an error message if the circuit is open, else None."""
    global _open_until
    if _open_until and time.time() < _open_until:
        return (
            f"serp-api circuit breaker open for provider '{provider}' "
            f"(too many consecutive failures); retry in {int(_open_until - time.time())}s"
        )
    return None


def _record_success() -> None:
    global _fail_streak, _open_until
    _fail_streak = 0
    _open_until = 0.0


def _record_failure() -> None:
    global _fail_streak, _open_until
    _fail_streak += 1
    if _fail_streak >= _breaker_threshold():
        _open_until = time.time() + _breaker_reset_s()


def _request(url: str, method: str, body: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:  # noqa: S310 (trusted SERP APIs)
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def _call(url: str, method: str, body: bytes | None, headers: dict[str, str], provider: str) -> dict[str, Any]:
    err = _check_breaker(provider)
    if err:
        raise RuntimeError(err)
    last_err: Exception | None = None
    for attempt in range(_max_retries() + 1):
        try:
            data = _request(url, method, body, headers)
            _record_success()
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if _is_retryable(e.code) and attempt < _max_retries():
                time.sleep(min(2 ** attempt, 8))
                continue
            _record_failure()
            raise RuntimeError(f"{provider} HTTP {e.code}: {e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < _max_retries():
                time.sleep(min(2 ** attempt, 8))
                continue
            _record_failure()
            raise RuntimeError(f"{provider} network error: {e}") from e
    _record_failure()
    raise RuntimeError(f"{provider} exhausted retries: {last_err}")


def http_get_json(
    url: str,
    params: dict[str, Any],
    *,
    provider: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    full = f"{url}?{qs}" if qs else url
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    return _call(full, "GET", None, h, provider)


def http_post_json(url: str, body: dict[str, Any], headers: dict[str, str], *, provider: str) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Accept": "application/json", **headers}
    return _call(url, "POST", data, h, provider)
