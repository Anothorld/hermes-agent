"""Google SERP extraction — navigate to a Google search page and extract results.

Instagram discovery usually lands on ``/reel/<id>`` / ``/p/<id>`` organic rows
(no author in the path). After collecting SERP rows, optionally resolve those
content URLs to author handles so ``candidate_handles`` can reach dozens per
query instead of only profile URLs / @mentions.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, str(_INTERNAL_DIR))

from errors import DomChangedError  # noqa: E402
from risk_detector import raise_on_risk  # noqa: E402


_DEFAULT_MAX_RESULTS = 30
_HARD_MAX_RESULTS = 40
_DEFAULT_MAX_AUTHOR_RESOLVES = 20
_HARD_MAX_AUTHOR_RESOLVES = 30

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_MENTION_RE = re.compile(r"@([A-Za-z0-9._]{2,30})\b")
_CONTENT_ID_RE = re.compile(
    r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_RESERVED = frozenset({
    "reel", "reels", "p", "tv", "stories", "explore", "accounts",
    "direct", "about", "legal", "developer", "directory", "web",
})

# JS that extracts organic search results from a Google SERP page.
# Prefer #rso / #search / .tF2Cxc; fall back to any h3→ancestor→a[href].
_SERP_JS = """
(() => {
  const results = [];
  const seen = new Set();

  const push = (title, href, snippet) => {
    if (!href || !title || seen.has(href)) return;
    if (href.startsWith('/search') || href.includes('google.com/search')) return;
    seen.add(href);
    results.push({
      rank: results.length + 1,
      title: title.slice(0, 200),
      url: href,
      snippet: (snippet || '').slice(0, 300),
    });
  };

  const containers = document.querySelectorAll('#rso .tF2Cxc, #search .tF2Cxc, .tF2Cxc');
  if (containers.length > 0) {
    for (const el of containers) {
      const a = el.querySelector('a[href]');
      const h3 = el.querySelector('h3');
      if (!a || !h3) continue;
      const snipEl = el.querySelector('[data-sncf], .VwiC3b, .IsZvec, .s');
      push(h3.textContent.trim(), a.href, snipEl ? snipEl.textContent.trim() : '');
      if (results.length >= 40) break;
    }
  }

  if (results.length === 0) {
    for (const h3 of document.querySelectorAll('h3')) {
      let node = h3.parentElement;
      let a = null;
      for (let i = 0; i < 5 && node; i++) {
        if (node.tagName === 'A' && node.href) { a = node; break; }
        const inner = node.querySelector('a[href]');
        if (inner) { a = inner; break; }
        node = node.parentElement;
      }
      if (!a) continue;
      const block = h3.closest('div') || h3.parentElement;
      const snipEl = block ? block.querySelector('[data-sncf], .VwiC3b, .IsZvec') : null;
      push(h3.textContent.trim(), a.href, snipEl ? snipEl.textContent.trim() : '');
      if (results.length >= 40) break;
    }
  }
  return { results, count: results.length };
})()
"""

# Lightweight author resolve on an IG reel/post page (no comment parse).
_AUTHOR_JS = """
(() => {
  const bad = new Set(['reel','reels','p','tv','stories','explore','accounts','direct','about','legal','www']);
  const fromUrl = (u) => {
    const m = String(u || '').match(/instagram\\.com\\/([A-Za-z0-9._]+)\\/(?:reel|p|tv)\\//i);
    if (!m) return '';
    const h = m[1].toLowerCase();
    return bad.has(h) ? '' : h;
  };
  let handle = fromUrl((document.querySelector('meta[property="og:url"]') || {}).content)
    || fromUrl((document.querySelector('link[rel="canonical"]') || {}).href)
    || fromUrl(location.href);
  if (handle) return { handle, source: 'url' };
  const hrefRe = /^\\/[A-Za-z0-9._]+\\/$/;
  for (const a of document.querySelectorAll('header a[href], main a[href], a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!hrefRe.test(href)) continue;
    const text = (a.textContent || '').replace(/\\s+/g, ' ').trim()
      .replace(/已验证|verified/gi, '').trim().replace(/^@/, '');
    const h = href.replace(/\\//g, '');
    if (!text || text.toLowerCase() !== h.toLowerCase()) continue;
    if (bad.has(h.toLowerCase())) continue;
    return { handle: h.toLowerCase(), source: 'dom_link' };
  }
  return { handle: '', source: null };
})()
"""


def extract_ig_handles_from_serp(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive Instagram handles from SERP rows (profile URLs + @mentions).

    Does not open reel/post pages — use ``resolve_content_authors`` for those.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(handle: str, *, source: str, rank: int, evidence: str) -> None:
        h = (handle or "").strip().lstrip("@").lower()
        if not h or h in seen or h in _RESERVED or not _HANDLE_RE.match(h):
            return
        if h.endswith(".com") or h.endswith(".jpg") or len(h) < 2:
            return
        seen.add(h)
        out.append({
            "handle": h,
            "source": source,
            "serp_rank": rank,
            "evidence": (evidence or "")[:240],
        })

    for row in results or []:
        rank = int(row.get("rank") or 0)
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        try:
            path = urlparse(url).path or ""
        except Exception:
            path = ""
        parts = [p for p in path.split("/") if p]
        if parts and "instagram.com" in url.lower():
            first = parts[0].lower()
            if first not in _RESERVED and _HANDLE_RE.match(first):
                if len(parts) == 1 or (
                    len(parts) >= 2 and parts[1].lower() not in {"reel", "p", "tv", "reels"}
                ):
                    _add(first, source="profile_url", rank=rank, evidence=url)
        for text, src in ((title, "title_mention"), (snippet, "snippet_mention")):
            for m in _MENTION_RE.finditer(text):
                _add(m.group(1), source=src, rank=rank, evidence=text)

    out.sort(key=lambda x: (x.get("serp_rank") or 999, x.get("handle") or ""))
    return out


def collect_content_shortcodes(results: list[dict[str, Any]]) -> list[str]:
    """Unique reel/post shortcodes from SERP result URLs (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for row in results or []:
        url = str(row.get("url") or "")
        m = _CONTENT_ID_RE.search(url)
        if not m:
            continue
        code = m.group(1)
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def resolve_content_authors(
    runner,
    shortcodes: list[str],
    *,
    max_resolve: int = _DEFAULT_MAX_AUTHOR_RESOLVES,
) -> dict[str, Any]:
    """Open IG ``/p/<id>/`` pages and extract author handles.

    Args:
        runner: ``CdpRunner`` instance (same Chrome session as SERP).
        shortcodes: Reel/post ids from SERP URLs.
        max_resolve: Cap navigations this call (default 20, hard max 30).

    Returns:
        Dict with ``authors`` (deduped by handle) and ``navigations`` attempted.
    """
    import pacing
    import random as _random
    import time as _time

    max_resolve = max(0, min(int(max_resolve), _HARD_MAX_AUTHOR_RESOLVES))
    if max_resolve <= 0 or not shortcodes:
        return {"authors": [], "navigations": 0}

    seen_handles: set[str] = set()
    authors: list[dict[str, Any]] = []
    navigations = 0
    for code in shortcodes[:max_resolve]:
        content_url = f"https://www.instagram.com/p/{code}/"
        pacing.jitter_delay("reel")
        pacing.mark_reel_load(getattr(runner, "task_id", "") or "")
        resp = runner.navigate(content_url)
        navigations += 1
        if not resp.get("success"):
            continue
        snapshot_text = (resp.get("data") or {}).get("snapshot", "") or ""
        raise_on_risk(snapshot_text)
        _time.sleep(_random.uniform(0.45, 0.85))
        raw = runner.eval(_AUTHOR_JS)
        if not isinstance(raw, dict):
            continue
        handle = str(raw.get("handle") or "").strip().lstrip("@").lower()
        if not handle or handle in seen_handles or handle in _RESERVED:
            continue
        if not _HANDLE_RE.match(handle):
            continue
        seen_handles.add(handle)
        authors.append({
            "handle": handle,
            "source": f"reel_author:{raw.get('source') or 'unknown'}",
            "serp_rank": 0,
            "evidence": content_url,
            "shortcode": code,
            "content_url": content_url,
        })
    return {"authors": authors, "navigations": navigations}


def fetch_serp(
    runner,
    query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
    *,
    resolve_authors: bool = True,
    max_author_resolves: int = _DEFAULT_MAX_AUTHOR_RESOLVES,
) -> dict:
    """Navigate to Google search and extract SERP results + IG candidates.

    Args:
        runner: A ``CdpRunner`` instance.
        query: Search query string (will be URL-encoded).
        max_results: Max organic results to return (capped at 40).
        resolve_authors: When True, open reel/post URLs to recover author handles.
        max_author_resolves: Max content pages to open per SERP call.

    Returns:
        Dict with ``results``, ``candidate_handles`` (mentions + resolved authors),
        and resolve stats.

    Raises:
        DomChangedError: If JS extraction returns empty data.
    """
    import pacing

    max_results = max(1, min(int(max_results), _HARD_MAX_RESULTS))
    encoded = quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}&num={max_results}"

    pacing.jitter_delay("profile")

    resp = runner.navigate(url)
    if not resp.get("success"):
        raise DomChangedError(f"navigate to google failed: {resp.get('error')}")

    import random as _random
    import time as _time
    _time.sleep(_random.uniform(0.8, 1.2))

    # Extra scrolls — Google often hydrates ~8–10 rows until the page is scrolled.
    try:
        runner.scroll(times=3, direction="down")
        _time.sleep(_random.uniform(0.5, 0.9))
    except Exception:
        pass

    js_result = runner.eval(_SERP_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS SERP extraction returned empty or non-dict result")

    results = js_result.get("results", [])[:max_results]
    candidate_handles = extract_ig_handles_from_serp(results)

    shortcodes = collect_content_shortcodes(results)
    resolved: list[dict[str, Any]] = []
    author_navigations = 0
    if resolve_authors and shortcodes:
        resolve_out = resolve_content_authors(
            runner, shortcodes, max_resolve=max_author_resolves,
        )
        resolved = list(resolve_out.get("authors") or [])
        author_navigations = int(resolve_out.get("navigations") or 0)
        seen = {h["handle"] for h in candidate_handles}
        for row in resolved:
            if row["handle"] in seen:
                continue
            seen.add(row["handle"])
            candidate_handles.append({
                "handle": row["handle"],
                "source": row["source"],
                "serp_rank": row.get("serp_rank") or 0,
                "evidence": row.get("evidence") or "",
            })
        candidate_handles.sort(
            key=lambda x: (x.get("serp_rank") or 999, x.get("handle") or ""),
        )

    diagnostic = None
    if not results:
        diag = runner.eval(
            "(() => ({"
            "title: document.title, "
            "url: location.href, "
            "bodyLen: document.body ? document.body.innerText.length : 0, "
            "hasRso: !!document.querySelector('#rso'), "
            "hasSearch: !!document.querySelector('#search'), "
            "h3Count: document.querySelectorAll('h3').length, "
            "tF2CxcCount: document.querySelectorAll('.tF2Cxc').length, "
            "hasConsent: /consent|Before you continue|Our privacy/i.test(document.body?.innerText||''), "
            "hasCaptcha: /captcha|unusual traffic|验证/i.test(document.body?.innerText||'')"
            "}))()"
        )
        diagnostic = diag

    return {
        "data": {
            "query": query,
            "results": results,
            "count": len(results),
            "candidate_handles": candidate_handles,
            "candidate_handle_count": len(candidate_handles),
            "content_urls_found": len(shortcodes),
            "authors_resolved": len(resolved),
            "author_navigations": author_navigations,
            "resolve_authors": bool(resolve_authors),
            "diagnostic": diagnostic,
        },
        "errors": [],
    }
