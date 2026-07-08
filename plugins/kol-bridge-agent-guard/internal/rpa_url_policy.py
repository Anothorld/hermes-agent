"""URL policy for RPA browser block — which URLs to block/allow for browser_*.

When ``KOL_RPA_STRICT_BROWSER_BLOCK=1`` (default), browser_navigate to
Instagram, Google search, and ipinfo URLs is blocked in campaign discovery
sessions because RPA tools replace those. Curated lists, TikTok, Reddit,
and link-in-bio URLs remain allowed (not covered by RPA).

Google is blocked only for ``/search`` paths — non-search Google pages
(e.g. Google Docs, Google Maps) are allowed.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Domains fully blocked (all paths)
_BLOCKED_DOMAINS_FULL = frozenset({
    "instagram.com",
    "www.instagram.com",
    "ipinfo.io",
})

# Domains blocked only for specific paths
# (host, path_prefix) — block if path starts with prefix
_BLOCKED_PATH_PREFIXES = {
    "google.com": "/search",
    "www.google.com": "/search",
}

# Domains explicitly allowed (not covered by RPA)
_ALLOWED_DOMAINS = frozenset({
    "feedspot.com",
    "www.feedspot.com",
    "influencerhero.com",
    "www.influencerhero.com",
    "tiktok.com",
    "www.tiktok.com",
    "reddit.com",
    "www.reddit.com",
    "linktr.ee",
    "beacons.ai",
    "bio.link",
    "lnk.bio",
    "solo.to",
    "shopmy.us",
    "ltk.app",
})


def should_block_url(url: str) -> bool:
    """Return True if browser navigation to this URL should be blocked.

    Args:
        url: The target URL from browser_navigate args.

    Returns:
        True if the URL is in the RPA-replaced blocklist.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
        # Strip port if present
        host = host.split(":")[0]
        path = parsed.path or "/"

        # Full domain block (instagram.com, ipinfo.io)
        if host in _BLOCKED_DOMAINS_FULL:
            return True

        # Path-specific block (google.com/search only)
        if host in _BLOCKED_PATH_PREFIXES:
            prefix = _BLOCKED_PATH_PREFIXES[host]
            if path.startswith(prefix):
                return True

        return False
    except Exception:
        return False


def is_explicitly_allowed(url: str) -> bool:
    """Return True if the URL domain is in the explicit allowlist."""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower().split(":")[0]
        return host in _ALLOWED_DOMAINS
    except Exception:
        return False
