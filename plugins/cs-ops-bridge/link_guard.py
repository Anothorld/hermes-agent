"""Link guard — blocks AI drafts from containing unverified (404) povison.com links.

Scans draft HTML content for povison.com URLs (both href attributes and plain
text), performs a HEAD request to each, and blocks the draft-save if any URL
returns 404 or 5xx. This prevents the AI from sending customers broken links
constructed by guessing URL slugs from Hindsight descriptions or product names.

Architecture mirrors compensation_guard.py:
  - URL extraction via regex on raw HTML (catches href="..." and bare URLs)
  - HEAD request with short timeout + redirect following
  - Returns block payload with matched URLs + HTTP status codes

Callers: draft_guard.py guard_draft_content() — guard #4.
"""

from __future__ import annotations

import os
import re
import urllib.request
import urllib.error
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────

_ENABLED = os.environ.get("CS_OPS_LINK_GUARD_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)
_TIMEOUT = float(os.environ.get("CS_OPS_LINK_GUARD_TIMEOUT", "5"))
# Only verify povison.com links (other domains are out of scope)
_DOMAIN_FILTER = "povison.com"

# ── URL extraction ──────────────────────────────────────────────────────

# Match href="https://www.povison.com/..." and bare https://www.povison.com/...
_HREF_RE = re.compile(
    r'(?:href=["\']|)(https?://[^\s"\'<>]*' + re.escape(_DOMAIN_FILTER) + r'[^\s"\'<>]*)',
    re.IGNORECASE,
)


def _extract_povison_urls(html: str) -> list[str]:
    """Extract unique povison.com URLs from HTML content."""
    if not html or not html.strip():
        return []
    found = _HREF_RE.findall(str(html))
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        # Strip trailing punctuation that might be part of sentence, not URL
        clean = url.rstrip(".,);>")
        if clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def _check_url(url: str, timeout: float = _TIMEOUT) -> int | None:
    """Perform a HEAD request and return the HTTP status code.

    Returns None if the request fails (network error, timeout).
    Follows redirects (urllib does this by default for HTTPRedirectHandler).
    """
    # Create a request with HEAD method (lighter than GET)
    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": "Povison-CS-LinkGuard/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        # HTTPError still has a status code (404, 500, etc.)
        return e.code
    except (urllib.error.URLError, OSError, Exception):
        # Network error, DNS failure, timeout — don't block, just can't verify
        return None


# ── Guard interface ─────────────────────────────────────────────────────

def check_content(content: str) -> dict[str, Any]:
    """Check draft HTML content for broken povison.com links.

    Returns a dict:
      - ``blocked`` (bool): True if any povison.com URL returns 404 or 5xx
      - ``matches`` (list[str]): Human-readable list of broken URLs + status
      - ``snippet`` (str): Context around the first broken URL
    """
    if not content or not str(content).strip():
        return {"blocked": False, "matches": [], "snippet": ""}

    if not _ENABLED:
        return {"blocked": False, "matches": [], "snippet": ""}

    urls = _extract_povison_urls(str(content))
    if not urls:
        return {"blocked": False, "matches": [], "snippet": ""}

    broken: list[str] = []
    for url in urls:
        status = _check_url(url)
        if status is not None and (status == 404 or status >= 500):
            broken.append(f"{url} [HTTP {status}]")

    if not broken:
        return {"blocked": False, "matches": [], "snippet": ""}

    # Build snippet from first broken URL
    first_url = broken[0].split(" [HTTP")[0]
    idx = str(content).find(first_url)
    if idx >= 0:
        start = max(0, idx - 60)
        end = min(len(str(content)), idx + len(first_url) + 60)
        snippet = str(content)[start:end].replace("\n", " ")
    else:
        snippet = ""

    return {
        "blocked": True,
        "matches": broken,
        "snippet": snippet,
    }


def guard_draft(content: str) -> dict[str, Any]:
    """Check draft content for broken povison.com links.

    Returns a dict with:
      - ``blocked`` (bool)
      - ``matches`` (list[str]): broken URLs with HTTP status
      - ``snippet`` (str): context around first broken URL
      - ``error`` (str): Ready-to-print error message if blocked
    """
    result = check_content(content)

    if not result["blocked"]:
        return {"blocked": False, "matches": [], "snippet": "", "error": ""}

    error_msg = (
        f"Link guard: draft blocked — broken povison.com link(s) detected. "
        f"Matched: {', '.join(result['matches'])}. "
        f"Remove or replace the broken link(s) before saving. "
        f"To get the correct product URL, run product_lookup.py --search \"<product name>\" "
        f"and use the detailUrl field from the output. "
        f"If you cannot verify a product URL, link to the collection page only or omit the link."
    )

    return {
        "blocked": True,
        "matches": result["matches"],
        "snippet": result["snippet"],
        "error": error_msg,
    }
