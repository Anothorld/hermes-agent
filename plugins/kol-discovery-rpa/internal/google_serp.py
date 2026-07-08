"""Google SERP extraction — navigate to google.com/search and extract results.

Extracts title, URL, snippet, and rank from Google search results.
Used for public-web KOL discovery (cross-vertical seed expansion).
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote_plus

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import DomChangedError  # noqa: E402


_SERP_JS = """
(() => {
  const results = [];
  const seen = new Set();

  // Google results always carry an <h3> title inside an anchor. Anchoring on
  // h3 is far more stable than container selectors (div.g was retired; the
  // current layout uses .tF2Cxc but that can change). We collect candidate
  // (title, url) pairs from h3→closest a[href], then dedup by url.
  const isGoogleNav = (href) => {
    if (!href) return true;
    if (href.startsWith('javascript:')) return true;
    if (href.startsWith('#')) return true;
    // Google-owned surfaces that appear in the SERP chrome but aren't results
    if (/^https?:\\/\\/(?:www\\.)?google\\.(?:com|[a-z]{2,3})\\//i.test(href)) return true;
    if (/^https?:\\/\\/(?:support|accounts|maps|policies|play)\\.google\\.com/i.test(href)) return true;
    if (/^https?:\\/\\/(?:www\\.)?google\\.[a-z]{2,3}\\//i.test(href)) return true;
    return false;
  };

  // Decode /url?q=... redirect to the real destination
  const unwrapGoogleRedirect = (href) => {
    const m = href.match(/[?&](?:q|url)=([^&]+)/);
    if (m) {
      try { return decodeURIComponent(m[1]); } catch { return href; }
    }
    return href;
  };

  // Primary path: h3 titles → enclosing anchor
  const h3s = document.querySelectorAll('#rso h3, #search h3, .tF2Cxc h3');
  for (const h3 of h3s) {
    const a = h3.closest('a[href]') || h3.parentElement?.closest('a[href]');
    if (!a) continue;
    let href = a.href || '';
    if (isGoogleNav(href)) {
      const unwrapped = unwrapGoogleRedirect(href);
      if (isGoogleNav(unwrapped)) continue;
      href = unwrapped;
    }
    if (seen.has(href)) continue;
    seen.add(href);

    const title = h3.textContent?.trim() || '';
    // Snippet: walk up to the result container, then look for the description
    let snippet = '';
    const container = a.closest('.tF2Cxc, [data-ved], div.g, div[data-sokoban-container]');
    if (container) {
      const sn = container.querySelector(
        '.VwiC3b, .IsZvec, [data-content-feature="1"], span.aCOpRe, [style*="-webkit-line-clamp"]'
      );
      snippet = sn?.textContent?.trim() || '';
    }
    results.push({ rank: 0, title: title.slice(0, 200), url: href, snippet: snippet.slice(0, 500) });
  }

  // Fallback: .tF2Cxc containers with a leading non-nav anchor (older layout)
  if (results.length === 0) {
    const cards = document.querySelectorAll('.tF2Cxc, div.g');
    for (const card of cards) {
      const a = card.querySelector('a[href]');
      if (!a) continue;
      let href = a.href || '';
      if (isGoogleNav(href)) {
        const unwrapped = unwrapGoogleRedirect(href);
        if (isGoogleNav(unwrapped)) continue;
        href = unwrapped;
      }
      if (seen.has(href)) continue;
      seen.add(href);
      const title = card.querySelector('h3')?.textContent?.trim() || a.textContent?.trim() || '';
      const sn = card.querySelector('.VwiC3b, .IsZvec, [data-content-feature="1"], span.aCOpRe');
      results.push({ rank: 0, title: title.slice(0, 200), url: href, snippet: (sn?.textContent || '').slice(0, 500) });
    }
  }

  // Assign ranks after dedup
  results.forEach((r, i) => { r.rank = i + 1; });
  return { results, count: results.length };
})()
"""


def fetch_serp(runner, query: str, max_results: int = 20) -> dict:
    """Navigate to Google search and extract SERP results.

    Args:
        runner: A ``CdpRunner`` instance.
        query: Search query string (will be URL-encoded).
        max_results: Max results to return.

    Returns:
        Dict with ``results`` list (each: rank, title, url, snippet).

    Raises:
        DomChangedError: If JS extraction returns empty data.
    """
    import pacing

    encoded = quote_plus(query)
    url = f"https://www.google.com/search?q={encoded}"

    pacing.jitter_delay("profile")

    resp = runner.navigate(url)
    if not resp.get("success"):
        raise DomChangedError(f"navigate to google failed: {resp.get('error')}")

    # Google's SPA hydrates results after navigate's readyState check passes —
    # the first eval otherwise returns 0 results. Settle ~1s before extracting.
    import random as _random
    import time as _time
    _time.sleep(_random.uniform(0.8, 1.2))

    js_result = runner.eval(_SERP_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS SERP extraction returned empty or non-dict result")

    results = js_result.get("results", [])[:max_results]

    # When results are empty, capture a lightweight diagnostic so the agent
    # can distinguish "Google changed DOM" from "captcha/consent" without
    # needing a browser fallback.
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
            "diagnostic": diagnostic,
        },
        "errors": [],
    }
