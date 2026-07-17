"""Placement URL liveness guard for SEO Studio.

The pattern validator in ``server.py._validate_placement_urls`` catches
obviously-fabricated URLs, but a URL can match the povison PDP/blog shape and
still 404 (product retired, article unpublished, slug typo). This module does
the actual HTTP reachability check — the second guard rail.

- ``check_url_live(url)`` — single URL: HEAD (fallback GET), follow redirects,
  timeout 10s. Returns ``{url, live, status_code, final_url, error}``.
- ``check_urls(urls, workers)`` — parallel batch (default 6 workers). Returns
  ``{ok, checked_at, results: [...], dead_count}``.

Host whitelist: ``*.povison.com`` (both ``www.povison.com`` PDPs and
``static.povison.com`` images). Non-povison hosts are rejected without a
network call (defense in depth — placements should never link off-povison).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests

_UA = "seo-studio-placement-guard/1.0"
_TIMEOUT = 10
_HOST_RE = re.compile(r"^(?:[\w-]+\.)*povison\.com$", re.I)
# Treat these as live (2xx + 3xx). 4xx/5xx + network errors = dead.
_LIVE_MIN = 200
_LIVE_MAX = 399


def _is_povison(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(_HOST_RE.match(host))


def check_url_live(url: str) -> dict[str, Any]:
    """Check a single URL's liveness. Never raises.

    Tries HEAD first (cheap), falls back to GET if the server rejects HEAD.
    Follows redirects. Returns:

    - ``url``: the input URL
    - ``live``: True if final status is 2xx/3xx
    - ``status_code``: int or None (on network error)
    - ``final_url``: URL after redirects (may differ)
    - ``error``: short error string when not live / not povison / network fail
    """
    url = (url or "").strip()
    if not url:
        return {"url": "", "live": False, "status_code": None, "final_url": "", "error": "empty url"}
    if not _is_povison(url):
        return {"url": url, "live": False, "status_code": None, "final_url": url, "error": "host is not povison.com — off-domain placements forbidden"}
    headers = {"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"}
    for method in ("HEAD", "GET"):
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=_TIMEOUT,
                allow_redirects=True, stream=(method == "GET"),
            )
            code = resp.status_code
            final = resp.url
            # Consume / close the GET body so we don't hold the connection.
            if method == "GET":
                resp.close()
            if _LIVE_MIN <= code <= _LIVE_MAX:
                return {"url": url, "live": True, "status_code": code, "final_url": final, "error": ""}
            # Non-2xx/3xx — record and (for HEAD) try GET as a fallback since
            # some servers return 405 for HEAD.
            if method == "HEAD" and code in (405, 403, 400):
                continue
            return {"url": url, "live": False, "status_code": code, "final_url": final, "error": f"HTTP {code}"}
        except requests.RequestException as exc:
            # Try GET next if HEAD errored; otherwise it's dead.
            if method == "HEAD":
                continue
            return {"url": url, "live": False, "status_code": None, "final_url": url, "error": f"network: {type(exc).__name__}"}
    return {"url": url, "live": False, "status_code": None, "final_url": url, "error": "all request methods failed"}


def check_urls(urls: list[str], *, workers: int = 6) -> dict[str, Any]:
    """Parallel liveness check for a batch of URLs.

    Args:
        urls: List of URLs (deduped, empties dropped).
        workers: Concurrent workers (1-12).

    Returns:
        ``{ok, checked_at, total, dead_count, results: [{url, live, status_code, final_url, error}]}``
    """
    workers = max(1, min(int(workers or 6), 12))
    # Dedupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls or []:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    results: list[dict[str, Any]] = [None] * len(uniq)  # type: ignore[list-item]
    if not uniq:
        return {"ok": True, "checked_at": time.time(), "total": 0, "dead_count": 0, "results": []}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_idx = {ex.submit(check_url_live, u): i for i, u in enumerate(uniq)}
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[i] = {"url": uniq[i], "live": False, "status_code": None, "final_url": uniq[i], "error": f"guard: {type(exc).__name__}"}
    dead = sum(1 for r in results if r and not r.get("live"))
    return {
        "ok": True,
        "checked_at": time.time(),
        "total": len(results),
        "dead_count": dead,
        "results": results,
    }


def health() -> dict[str, Any]:
    """Lightweight reachability check (probes one known-good povison URL)."""
    try:
        r = check_url_live("https://www.povison.com/blog/")
        return {"ok": bool(r.get("live")), "probe_live": bool(r.get("live")), "probe_status": r.get("status_code")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
