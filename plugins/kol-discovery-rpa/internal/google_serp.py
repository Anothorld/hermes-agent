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
  // Google search result selectors — multiple paths for resilience
  const selectors = [
    'div.g div[data-sokoban-container] a[href]',
    'div.g a[href]',
    'div[data-ved] a[href]',
    '.tF2Cxc a[href]',
  ];
  let links = [];
  for (const sel of selectors) {
    const found = document.querySelectorAll(sel);
    if (found.length > 0) {
      links = Array.from(found);
      break;
    }
  }

  let rank = 0;
  for (const link of links.slice(0, 20)) {
    const href = link.href || '';
    if (!href || href.startsWith('https://www.google.com/') || href.startsWith('https://google.com/')) continue;
    if (href.startsWith('javascript:')) continue;

    rank++;
    const title = link.textContent?.trim() || '';

    // Find parent container for snippet
    let snippet = '';
    let parent = link.closest('div.g, div[data-ved], .tF2Cxc');
    if (parent) {
      const snippetEl = parent.querySelector('.VwiC3b, .IsZvec, [data-content-feature="1"], span.aCOpRe');
      snippet = snippetEl?.textContent?.trim() || '';
    }

    results.push({
      rank: rank,
      title: title.slice(0, 200),
      url: href,
      snippet: snippet.slice(0, 500),
    });
  }

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

    js_result = runner.eval(_SERP_JS)
    if not js_result or not isinstance(js_result, dict):
        raise DomChangedError("JS SERP extraction returned empty or non-dict result")

    results = js_result.get("results", [])[:max_results]

    return {
        "data": {
            "query": query,
            "results": results,
            "count": len(results),
        },
        "errors": [],
    }
