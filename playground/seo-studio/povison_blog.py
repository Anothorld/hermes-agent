"""POVISON blog article catalog for SEO Studio internal-link placements.

Internal links in POVISON SEO blog posts MUST point to real articles under
``https://www.povison.com/blog/``. The agent previously fabricated blog URLs
(404). This module is the deterministic source of truth:

- ``fetch_sitemap()`` — pull + cache ``sitemap_blogs_1.xml`` (TTL ~6h, file cache)
- ``search_articles(keyword, limit)`` — keyword match on URL slug, returns
  candidates ``{url, slug, title_guess, category}``
- ``recommend_links(topic, sections, existing_urls, limit)`` — build queries
  from topic primary/secondary + H2 titles, search, dedupe, pick 2-3, return
  links with anchor suggestions
- ``verify_url(url)`` — is this URL a real povison blog article?
- ``health()`` — reachability check

No API token required. Host whitelist: ``*.povison.com``.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

_UA = "seo-studio-povison-blog/1.0"
_TIMEOUT = 25
_SITEMAP_URL = "https://www.povison.com/sitemap_blogs_1.xml"
_BASE = "https://www.povison.com/"
_HOST_RE = re.compile(r"^(?:.*\.)?povison\.com$", re.I)

# File cache (lives next to the module so Bridge / CLI share it).
_CACHE_FILE = Path(__file__).resolve().parent / ".cache" / "blog_sitemap.xml"
_CACHE_TTL = 6 * 3600  # 6 hours

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with",
    "your", "you", "is", "are", "how", "what", "why", "when", "which",
    "best", "top", "vs", "guide", "tips", "tip", "buying", "review", "reviews",
    "povison", "blog", "html", "from", "by", "this", "that", "these", "those",
}


def _is_povison(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(_HOST_RE.match(host))


def _slugify_title(slug: str) -> str:
    """Turn a URL slug into a human-ish title guess.

    ``sofa-bed-vs-sleeper-sofa-essential-differences`` →
    ``Sofa Bed vs Sleeper Sofa Essential Differences``
    """
    parts = [p for p in slug.split("/") if p]
    last = parts[-1] if parts else ""
    last = last.removesuffix(".html")
    words = [w for w in re.split(r"[-_]", last) if w]
    if not words:
        return ""
    title = " ".join(w.capitalize() if w not in {"vs"} else w for w in words)
    return title


def _category_from_url(url: str) -> str:
    path = (urlparse(url).path or "").strip("/")
    parts = [p for p in path.split("/") if p]
    # /blog/<category>/<slug>.html
    if len(parts) >= 3 and parts[0] == "blog":
        return parts[1]
    return ""


def _fetch_url(url: str) -> str:
    headers = {"User-Agent": _UA, "Accept-Encoding": "gzip, identity"}
    resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_sitemap(xml: str) -> list[dict]:
    """Extract <loc> URLs from a sitemap XML. Only povison.com/blog/ entries."""
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    out = []
    for u in urls:
        u = html.unescape(u.strip())
        if not _is_povison(u):
            continue
        path = (urlparse(u).path or "").strip("/")
        if not path.startswith("blog/"):
            continue
        if not path.endswith(".html"):
            continue
        slug = path[len("blog/"):]
        out.append({
            "url": u,
            "slug": slug,
            "title_guess": _slugify_title(slug),
            "category": _category_from_url(u),
        })
    return out


def fetch_sitemap(*, force: bool = False) -> dict[str, Any]:
    """Return ``{ok, count, cached, articles}`` — fetches + caches the blog sitemap.

    Uses a file cache with TTL (``_CACHE_TTL``). Pass ``force=True`` to bypass.
    """
    if not force and _CACHE_FILE.exists():
        age = time.time() - _CACHE_FILE.stat().st_mtime
        if age < _CACHE_TTL:
            try:
                articles = _parse_sitemap(_CACHE_FILE.read_text(encoding="utf-8"))
                return {"ok": True, "count": len(articles), "cached": True, "articles": articles}
            except Exception:
                pass  # fall through to re-fetch
    try:
        xml = _fetch_url(_SITEMAP_URL)
    except Exception as exc:
        # Serve stale cache if network fails
        if _CACHE_FILE.exists():
            try:
                articles = _parse_sitemap(_CACHE_FILE.read_text(encoding="utf-8"))
                return {"ok": True, "count": len(articles), "cached": True, "stale": True, "articles": articles}
            except Exception:
                pass
        return {"ok": False, "error": str(exc), "articles": []}
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(xml, encoding="utf-8")
    except Exception:
        pass  # caching is best-effort
    articles = _parse_sitemap(xml)
    return {"ok": True, "count": len(articles), "cached": False, "articles": articles}


def _tokens(text: str) -> set[str]:
    """Lowercase alphabetic tokens, stopwords removed."""
    out = set()
    for w in re.split(r"[^a-z0-9]+", (text or "").lower()):
        w = w.strip()
        if w and w not in _STOPWORDS and len(w) > 2:
            out.add(w)
    return out


def _score(article: dict, query_tokens: set[str]) -> tuple[float, list[str]]:
    """Score an article against query tokens using slug + title + category."""
    slug = (article.get("slug") or "").lower().replace(".html", "").replace("-", " ")
    title = (article.get("title_guess") or "").lower()
    cat = (article.get("category") or "").lower()
    haystack = f"{slug} {title} {cat}"
    hay_tokens = _tokens(haystack)
    if not query_tokens or not hay_tokens:
        return 0.0, []
    hits = query_tokens & hay_tokens
    if not hits:
        return 0.0, []
    # Weight: slug hits count more than category
    slug_tokens = _tokens(slug)
    slug_hits = query_tokens & slug_tokens
    score = len(hits) / len(query_tokens)
    if slug_hits:
        score += 0.25 * (len(slug_hits) / len(query_tokens))
    reasons = []
    if slug_hits:
        reasons.append("slug match: " + ", ".join(sorted(slug_hits)))
    other = hits - slug_hits
    if other:
        reasons.append("title/category match: " + ", ".join(sorted(other)))
    return min(score, 1.0), reasons


def search_articles(keyword: str, *, limit: int = 10) -> dict[str, Any]:
    """Search cached blog articles by keyword. Returns ranked candidates.

    Args:
        keyword: Free-text query (e.g. "sofa bed materials").
        limit: Max candidates (1-50).

    Returns:
        ``{ok, keyword, total, candidates: [{url, slug, title_guess, category, score, reasons}]}``
    """
    limit = max(1, min(int(limit or 10), 50))
    sm = fetch_sitemap()
    if not sm.get("ok"):
        return {"ok": False, "keyword": keyword, "error": sm.get("error", "sitemap unavailable"), "candidates": []}
    articles = sm.get("articles") or []
    q_tokens = _tokens(keyword)
    if not q_tokens:
        return {"ok": True, "keyword": keyword, "total": len(articles), "candidates": articles[:limit]}
    scored = []
    for a in articles:
        s, reasons = _score(a, q_tokens)
        if s > 0:
            scored.append({**a, "score": round(s, 3), "reasons": reasons})
    scored.sort(key=lambda x: (x.get("score", 0), len(x.get("slug", ""))), reverse=True)
    return {"ok": True, "keyword": keyword, "total": len(scored), "candidates": scored[:limit]}


def verify_url(url: str) -> dict[str, Any]:
    """Is ``url`` a real povison.com/blog/ article present in the sitemap?

    Returns ``{ok, verified, url, article?}``. Never raises.
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "verified": False, "error": "url is required"}
    if not _is_povison(url):
        return {"ok": False, "verified": False, "error": "host is not povison.com"}
    sm = fetch_sitemap()
    if not sm.get("ok"):
        return {"ok": False, "verified": False, "error": sm.get("error", "sitemap unavailable")}
    # Normalize: strip query/fragment, ensure trailing .html
    norm = (urlparse(url).path or "").strip("/")
    for a in sm.get("articles") or []:
        if (urlparse(a["url"]).path or "").strip("/") == norm:
            return {"ok": True, "verified": True, "url": a["url"], "article": a}
    return {"ok": True, "verified": False, "url": url, "error": "not in blog sitemap"}


def _build_queries(topic: dict, sections: list[dict]) -> list[str]:
    """Build 1-3 search queries from topic + H2 titles."""
    pk = (topic.get("primary_keyword") or "").strip()
    sk = topic.get("secondary_keywords") or []
    ck = topic.get("category_keywords") or []
    queries: list[str] = []
    if pk:
        queries.append(pk)
    # Combine primary with one strong secondary
    extra = [s for s in sk if s and s.lower() != pk.lower()]
    if pk and extra:
        queries.append(f"{pk} {extra[0]}")
    # H2-derived queries (skip Intro/Conclusion)
    for s in sections or []:
        if (s.get("type") or "").lower() in ("intro", "conclusion"):
            continue
        t = (s.get("title") or s.get("heading") or s.get("h2Title") or "").strip()
        if t:
            queries.append(t)
    # Category keyword fallback
    if not queries and ck:
        queries.extend(ck)
    # De-dup preserve order, cap at 6
    seen = set()
    uniq = []
    for q in queries:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq[:6]


def _anchor_from_slug(slug: str) -> str:
    """Derive a natural anchor text from a blog slug.

    ``sofa-bed-vs-sleeper-sofa-essential-differences`` →
    ``sofa bed vs sleeper sofa``
    """
    last = slug.split("/")[-1].removesuffix(".html")
    words = [w for w in re.split(r"[-_]", last) if w]
    # Drop trailing filler words
    while words and words[-1] in {"essential", "differences", "guide", "tips", "review", "reviews", "home", "you"}:
        words.pop()
    return " ".join(words).lower() if words else slug.replace("-", " ").replace(".html", "")


def recommend_links(
    topic: dict,
    sections: list[dict],
    *,
    existing_urls: list[str] | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """Full pipeline: queries → search → score → pick 2-3 internal links.

    Args:
        topic: ``{primary_keyword, secondary_keywords, category_keywords}``.
        sections: ``articleState.sections`` (used for H2 matching).
        existing_urls: URLs already in ``articleState.links`` — skipped to avoid dupes.
        limit: Max links (1-5).

    Returns:
        ``{ok, links: [{anchor, url, sectionId, note, score, reasons}], queries, weak_fit}``
    """
    limit = max(1, min(int(limit or 3), 5))
    queries = _build_queries(topic, sections)
    if not queries:
        return {"ok": False, "error": "no keywords available from topic"}

    existing = {u.strip() for u in (existing_urls or []) if u}
    seen_urls: set[str] = set()
    scored: list[dict] = []
    for q in queries:
        res = search_articles(q, limit=10)
        if not res.get("ok"):
            continue
        for c in res.get("candidates") or []:
            u = c.get("url") or ""
            if not u or u in seen_urls or u in existing:
                continue
            seen_urls.add(u)
            scored.append(c)

    if not scored:
        return {"ok": False, "error": "search returned no candidates", "queries": queries}

    # H2 titles for section matching
    h2_sections = [
        {"id": s.get("id"), "title": (s.get("title") or s.get("heading") or s.get("h2Title") or "")}
        for s in (sections or [])
        if (s.get("type") or "").lower() not in ("intro", "conclusion")
    ]

    scored.sort(key=lambda x: (x.get("score", 0), len(x.get("slug", ""))), reverse=True)
    chosen = scored[:limit]
    weak = any(c.get("score", 0) < 0.4 for c in chosen)

    used_sections: set[str] = set()

    def _pick_section(article: dict) -> str:
        slug_t = _tokens((article.get("slug") or "").replace("-", " "))
        ranked = []
        for s in h2_sections:
            st = _tokens(s["title"])
            hit = len(slug_t & st)
            # Prefer: more hits → unassigned section → shorter (more specific) title
            ranked.append((hit, 0 if s["id"] in used_sections else 1, -len(st), s["id"]))
        ranked.sort(reverse=True)
        sid = ranked[0][3] if ranked and ranked[0][0] > 0 else (h2_sections[0]["id"] if h2_sections else "")
        if sid:
            used_sections.add(sid)
        return sid

    links = []
    for c in chosen:
        links.append({
            "anchor": _anchor_from_slug(c.get("slug") or ""),
            "url": c.get("url") or "",
            "sectionId": _pick_section(c),
            "note": "Real POVISON blog article (from sitemap). " + ("; ".join(c.get("reasons") or [])),
            "status": "pending",
            "score": c.get("score"),
            "reasons": c.get("reasons"),
            "category": c.get("category"),
            "title_guess": c.get("title_guess"),
            "source": "blog-sitemap",
        })
    return {"ok": True, "links": links, "queries": queries, "weak_fit": weak}


def health() -> dict[str, Any]:
    """Lightweight reachability check (no secrets)."""
    try:
        sm = fetch_sitemap(force=True)
        return {"ok": bool(sm.get("ok")), "sitemap_reachable": bool(sm.get("ok")), "count": sm.get("count", 0)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
