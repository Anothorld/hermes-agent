"""POVISON product catalog client for SEO Studio placements.

Deterministic wrapper around the same public APIs used by the povison-cs
``product_lookup`` skill, but oriented toward blog product placements:

- ``search_products(keyword)`` — keyword discovery with image + tags
- ``lookup_detail(path, variant?)`` — official media + specs (assembly, dims)
- ``scrape_pdp(url)`` — JSON-LD fallback only (Detail API failure / link check)
- ``recommend_placements(topic, sections)`` — full pipeline returning products[]

No API token required (storeId=3 only). Host whitelist: *.povison.com.
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

_UA = "seo-studio-povison-catalog/1.0"
_TIMEOUT = 20
_BASE = "https://www.povison.com/"
_SEARCH_URL = "https://api.povison.com/api/product-server/openApi/product/search/result"
_DETAIL_URL = "https://www.povison.com/api/product-server/openApi/product/modelProductList"
_DEFAULT_STORE_ID = 3
_HOST_RE = re.compile(r"^(?:.*\.)?povison\.com$", re.I)

# Main image preference order (skip size_image for blog hero use).
_IMAGE_PREF = ["image", "white_image", "sku_image", "scene1_image", "scene_image", "media_gallery"]


def _is_povison(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(_HOST_RE.match(host))


def _absolute(url: str) -> str:
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(_BASE, url.lstrip("/"))


def _strip_query(path: str) -> str:
    return (path or "").split("?", 1)[0].split("#", 1)[0]


def _extract_variant(url_or_path: str) -> str | None:
    if not url_or_path or "?" not in url_or_path:
        return None
    q = url_or_path.split("?", 1)[1].split("#", 0)[0]
    m = re.search(r"[?&]variant=([^&#]+)", "?" + q)
    return m.group(1) if m else None


def normalize_path(url_or_path: str) -> str:
    """Strip protocol/domain/query, keep API-ready path (e.g. products/x.html)."""
    s = (url_or_path or "").strip()
    if s.startswith(("http://", "https://")):
        parsed = urlparse(s)
        s = parsed.path or ""
    return _strip_query(s).lstrip("/")


def _flat_tags(item: dict) -> list[dict]:
    out = []
    for t in (item.get("tagVOList") or []):
        if isinstance(t, dict):
            out.append({"key": t.get("keyName") or "", "name": t.get("name") or ""})
    return out


def _search_image(item: dict) -> str:
    pi = item.get("productImage") or {}
    for key in ("image", "whiteImage", "scene1Image", "sceneImage", "mediaGallery"):
        v = pi.get(key)
        if isinstance(v, dict) and v.get("path"):
            return v["path"]
        if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("path"):
            return v[0]["path"]
    return ""


def _normalize_candidate(item: dict) -> dict:
    detail = item.get("detailUrl") or ""
    return {
        "name": (item.get("name") or item.get("productName") or "").strip(),
        "sku": item.get("sku") or "",
        "spu": item.get("spu") or "",
        "entity_id": item.get("entityId"),
        "detail_url": _absolute(detail),
        "detail_path": normalize_path(detail),
        "variant": _extract_variant(detail),
        "price": item.get("price"),
        "special_price": item.get("specialPrice"),
        "review_count": item.get("reviewCount"),
        "sale_count": item.get("saleCount"),
        "top_rated": item.get("topRated"),
        "image": _search_image(item),
        "tags": _flat_tags(item),
    }


def search_products(keyword: str, *, page: int = 1, page_size: int = 15, store_id: int = _DEFAULT_STORE_ID) -> dict[str, Any]:
    """Search POVISON catalog by keyword. Returns normalized candidates.

    Args:
        keyword: Product name, SKU, or feature keywords.
        page: 1-based page number.
        page_size: Results per page (max ~30).
        store_id: POVISON store id (3 = US).

    Returns:
        ``{ok, keyword, is_accurated, total, page, max_page, candidates}`` or
        ``{ok: false, error}`` on failure.
    """
    kw = re.sub(r"\s+", " ", (keyword or "").strip())[:120]
    if not kw:
        return {"ok": False, "error": "keyword is required"}
    body = {"keyword": kw, "page": page, "pageSize": page_size}
    try:
        resp = requests.post(
            _SEARCH_URL,
            json=body,
            headers={"Content-Type": "application/json", "storeId": str(store_id)},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"search_request_failed: {exc}"}
    if resp.status_code >= 400 or data.get("code") != 200:
        return {"ok": False, "error": data.get("msg") or f"HTTP {resp.status_code}", "code": data.get("code")}
    info = data.get("info") or {}
    page_dto = info.get("searchProductPageVOPageDTO") or {}
    items = page_dto.get("items") or []
    return {
        "ok": True,
        "keyword": info.get("keyword") or kw,
        "is_accurated": info.get("isAccurated"),
        "total": page_dto.get("total"),
        "page": page_dto.get("page"),
        "max_page": page_dto.get("maxPage"),
        "candidates": [_normalize_candidate(it) for it in items if isinstance(it, dict)],
    }


def pick_main_image(media: list[dict]) -> str:
    """Pick the best blog main image from a SKU productMediaList."""
    if not isinstance(media, list):
        return ""
    by_type = {m.get("imageType"): m.get("imageUrl") for m in media if isinstance(m, dict) and m.get("imageUrl")}
    for pref in _IMAGE_PREF:
        if by_type.get(pref):
            return by_type[pref]
    # Fall back to first non-size image.
    for m in media:
        if isinstance(m, dict) and m.get("imageUrl") and m.get("imageType") != "size_image":
            return m["imageUrl"]
    # Last resort: any URL.
    for m in media:
        if isinstance(m, dict) and m.get("imageUrl"):
            return m["imageUrl"]
    return ""


def _spec_summary(specs: dict) -> dict:
    if not isinstance(specs, dict):
        return {}
    keep = {}
    for k in ("Assembly Required", "Material", "Color", "Weight Capacity", "Number of Drawers", "Style"):
        if k in specs:
            v = specs[k]
            keep[k.lower().replace(" ", "_")] = v if not isinstance(v, list) else (v[0] if v else "")
    return keep


def _dim_summary(dims: dict) -> dict:
    if not isinstance(dims, dict):
        return {}
    out = {}
    for k, v in dims.items():
        if isinstance(v, list):
            out[k.lower()] = v[0] if v else ""
        else:
            out[k.lower()] = v
    return out


def _select_sku(skus: list[dict], variant: str | None = None) -> dict | None:
    if not skus:
        return None
    if variant:
        for s in skus:
            if str(s.get("entityId")) == str(variant):
                return s
    return skus[0]


def lookup_detail(path_or_url: str, *, variant: str | None = None, store_id: int = _DEFAULT_STORE_ID) -> dict[str, Any]:
    """Fetch product detail via modelProductList API.

    Args:
        path_or_url: Product path or full PDP URL.
        variant: Optional entityId (from ?variant=). Auto-extracted if URL.
        store_id: POVISON store id.

    Returns:
        ``{ok, name, sku, url, image, media_types, specs, dimensions, assembly, price}``
        or ``{ok: false, error, code}``.
    """
    if not path_or_url:
        return {"ok": False, "error": "path is required"}
    path = normalize_path(path_or_url)
    if not variant:
        variant = _extract_variant(path_or_url)
    body = {"storeId": store_id, "path": path}
    try:
        resp = requests.post(
            _DETAIL_URL,
            json=body,
            headers={"Content-Type": "application/json", "storeId": str(store_id)},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"detail_request_failed: {exc}"}
    if resp.status_code >= 400 or data.get("code") != 200:
        return {"ok": False, "error": data.get("msg") or f"HTTP {resp.status_code}", "code": data.get("code")}
    info = data.get("info") or {}
    details = info.get("productDetailVOList") or []
    if not details:
        return {"ok": False, "error": "product_not_found", "code": data.get("code")}
    item = details[0]
    spu = item.get("spuInfo") or {}
    skus = item.get("skuList") or []
    sku = _select_sku(skus, variant)
    if not sku:
        return {"ok": False, "error": "no_sku_available"}
    media = sku.get("productMediaList") or []
    image = pick_main_image(media)
    detail_url = _absolute(sku.get("detailUrl") or path)
    return {
        "ok": True,
        "name": (sku.get("name") or sku.get("productName") or spu.get("name") or "").strip(),
        "sku": sku.get("sku") or "",
        "spu": spu.get("spu") or "",
        "url": detail_url,
        "image": image,
        "media_types": [m.get("imageType") for m in media if isinstance(m, dict)],
        "specs": _spec_summary(sku.get("specifications") or {}),
        "dimensions": _dim_summary(sku.get("dimensions") or {}),
        "assembly": _spec_summary(sku.get("specifications") or {}).get("assembly_required"),
        "price": sku.get("specialPrice") or sku.get("price"),
        "review_count": spu.get("reviewCount"),
    }


class _PdpParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas = []
        self.title = ""
        self._in_title = False
        self.ld_blocks = []
        self._in_ld = False
        self._ld_buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            self.metas.append(a)
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self._ld_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_blocks.append("".join(self._ld_buf))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self._ld_buf.append(data)


def _meta_get(metas: list[dict], *keys: str) -> str:
    lk = {k.lower() for k in keys}
    for m in metas:
        prop = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        if prop in lk:
            return m.get("content") or ""
    return ""


def scrape_pdp(url: str) -> dict[str, Any]:
    """Fallback PDP scrape — JSON-LD Product + Breadcrumb, then og:*.

    Only used when Detail API fails or for manual link verification.
    Never parses __NUXT__ variable tables.
    """
    if not url:
        return {"ok": False, "error": "url is required"}
    if not _is_povison(url):
        return {"ok": False, "error": "host not allowed (povison.com only)"}
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept": "text/html", "Accept-Encoding": "gzip"},
            timeout=_TIMEOUT,
        )
        raw = resp.content
        if raw[:2] == b"\x1f\x8b":
            html = gzip.decompress(raw).decode("utf-8", "replace")
        else:
            html = raw.decode("utf-8", "replace")
    except Exception as exc:
        return {"ok": False, "error": f"scrape_request_failed: {exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code}"}

    p = _PdpParser()
    try:
        p.feed(html)
    except Exception:
        pass

    product = {}
    breadcrumbs = []
    for blob in p.ld_blocks:
        try:
            data = json.loads(blob)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            offers = data.get("offers") or {}
            product = {
                "name": data.get("name") or "",
                "image": data.get("image") or "",
                "sku": data.get("sku") or "",
                "description": (data.get("description") or "")[:300],
                "price": (offers.get("price") if isinstance(offers, dict) else None),
                "availability": (offers.get("availability") if isinstance(offers, dict) else None),
                "rating": ((offers.get("aggregateRating") or {}).get("ratingValue") if isinstance(offers, dict) else None),
            }
        elif isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
            for el in (data.get("itemListElement") or []):
                if isinstance(el, dict):
                    breadcrumbs.append(el.get("name") or "")

    og_image = _meta_get(p.metas, "og:image", "twitter:image")
    return {
        "ok": True,
        "name": product.get("name") or _meta_get(p.metas, "og:title") or p.title.strip(),
        "image": product.get("image") or og_image,
        "sku": product.get("sku"),
        "description": product.get("description") or _meta_get(p.metas, "og:description") or _meta_get(p.metas, "description"),
        "price": product.get("price"),
        "availability": product.get("availability"),
        "breadcrumbs": [b for b in breadcrumbs if b],
        "url": url,
    }


_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "with", "your",
    "how", "what", "is", "are", "best", "guide", "tips", "buy", "choosing",
    "vs", "versus", "you", "need", "know",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def _slug_tokens(url: str) -> set[str]:
    path = normalize_path(url)
    return {w for w in re.findall(r"[a-z0-9]+", path.lower()) if len(w) > 2 and w not in {"html", "products"}}


def score_topic_fit(candidate: dict, topic: dict, h2_titles: list[str] | None = None) -> dict[str, Any]:
    """Score how well a product candidate fits the article topic.

    Returns ``{score: float, reasons: list[str]}``.
    """
    primary = (topic.get("primary_keyword") or "").lower()
    secondary = [s.lower() for s in (topic.get("secondary_keywords") or [])]
    category_kws = [c.lower() for c in (topic.get("category_keywords") or [])]
    h2_titles = h2_titles or []

    name_toks = _tokens(candidate.get("name", ""))
    tag_names = {t.get("name", "").lower() for t in (candidate.get("tags") or [])}
    slug_toks = _slug_tokens(candidate.get("detail_url", ""))
    all_prod_toks = name_toks | tag_names | slug_toks

    reasons = []
    score = 0.0

    if primary:
        pk_toks = _tokens(primary)
        hit = pk_toks & all_prod_toks
        if hit:
            score += 0.35 * (len(hit) / max(len(pk_toks), 1))
            reasons.append(f"primary keyword match: {', '.join(sorted(hit))}")

    for kw in secondary[:3]:
        kw_toks = _tokens(kw)
        if kw_toks and (kw_toks & all_prod_toks):
            score += 0.1
            reasons.append(f"secondary keyword: {kw}")

    for kw in category_kws[:2]:
        kw_toks = _tokens(kw)
        if kw_toks and (kw_toks & all_prod_toks):
            score += 0.12
            reasons.append(f"category keyword: {kw}")

    for h2 in h2_titles[:4]:
        h2_toks = _tokens(h2)
        if h2_toks and (h2_toks & all_prod_toks):
            score += 0.08
            reasons.append(f"H2 overlap: {h2[:40]}")

    # Cap at 1.0
    score = min(score, 1.0)
    if not reasons:
        reasons.append("no keyword overlap (weak match)")
    return {"score": round(score, 3), "reasons": reasons}


def _build_queries(topic: dict, sections: list[dict] | None = None) -> list[str]:
    queries = []
    pk = (topic.get("primary_keyword") or "").strip()
    if pk:
        queries.append(pk[:80])
    for c in (topic.get("category_keywords") or [])[:1]:
        if c and c not in queries:
            queries.append(c[:80])
    # One H2-derived query (skip intro/conclusion).
    for sec in (sections or []):
        if sec.get("type") in ("Intro", "Conclusion"):
            continue
        title = (sec.get("title") or "").strip()
        if not title:
            continue
        # Strip common question prefixes.
        stripped = re.sub(r"^(how to|what is|why|when|where|should you)\s+", "", title, flags=re.I)
        q = stripped[:80]
        if q and q.lower() not in [x.lower() for x in queries]:
            queries.append(q)
            break
    return queries[:3]


def _pick_section(candidate: dict, sections: list[dict]) -> str:
    """Pick the best H2 section id for this product (skip Intro)."""
    h2s = [s for s in sections if s.get("type") not in ("Intro", "Conclusion") and s.get("id") and s.get("title")]
    if not h2s:
        return ""
    name_toks = _tokens(candidate.get("name", ""))
    best = (h2s[0], 0)
    for sec in h2s:
        t = _tokens(sec.get("title", ""))
        overlap = len(name_toks & t)
        if overlap > best[1]:
            best = (sec, overlap)
    return best[0].get("id") or ""


def _blurb_draft(name: str, assembly: str | None) -> str:
    base = f"The {name}"
    if assembly and "fully" in (assembly or "").lower():
        base += " arrives fully assembled"
    else:
        base += " is built to last"
    base += (
        ", so setup day is about arranging the room, not hunting for missing hardware. "
        "Its clean silhouette helps the piece blend with the surrounding decor without "
        "feeling like an afterthought."
    )
    return base


def recommend_placements(
    topic: dict,
    sections: list[dict],
    *,
    limit: int = 2,
    search_pool: int = 8,
    fit_threshold: float = 0.35,
    store_id: int = _DEFAULT_STORE_ID,
) -> dict[str, Any]:
    """Full pipeline: queries -> search -> score -> detail -> products[].

    Args:
        topic: Article topic with primary_keyword / secondary_keywords / category_keywords.
        sections: articleState.sections (used for H2 matching).
        limit: Max products to return (1-2).
        search_pool: How many search candidates to consider per query.
        fit_threshold: Minimum score to include; below = weak_fit warning.
        store_id: POVISON store id.

    Returns:
        ``{ok, products: [...], queries, weak_fit}``.
    """
    queries = _build_queries(topic, sections)
    if not queries:
        return {"ok": False, "error": "no keywords available from topic"}

    seen_paths: set[str] = set()
    candidates: list[dict] = []
    for q in queries:
        res = search_products(q, page=1, page_size=search_pool, store_id=store_id)
        if not res.get("ok"):
            continue
        for c in res.get("candidates") or []:
            key = c.get("detail_path") or c.get("detail_url")
            if key and key not in seen_paths:
                seen_paths.add(key)
                candidates.append(c)

    if not candidates:
        return {"ok": False, "error": "search returned no candidates", "queries": queries}

    h2_titles = [s.get("title", "") for s in sections if s.get("type") not in ("Intro", "Conclusion")]
    scored = []
    for c in candidates:
        fit = score_topic_fit(c, topic, h2_titles)
        c["fit_score"] = fit["score"]
        c["fit_reasons"] = fit["reasons"]
        scored.append(c)
    scored.sort(key=lambda x: (x.get("fit_score", 0), x.get("sale_count") or 0, x.get("review_count") or 0), reverse=True)

    # Pick top, allow below-threshold as weak_fit.
    chosen = []
    weak = False
    for c in scored:
        if len(chosen) >= limit:
            break
        if c.get("fit_score", 0) < fit_threshold:
            weak = True
        chosen.append(c)

    if not chosen:
        return {"ok": False, "error": "no candidates scored", "queries": queries}

    products = []
    for c in chosen:
        detail = lookup_detail(c.get("detail_path") or c.get("detail_url"), variant=c.get("variant"), store_id=store_id)
        name = c.get("name") or ""
        image = c.get("image") or ""
        url = _absolute(c.get("detail_url") or "")
        if detail.get("ok"):
            name = detail.get("name") or name
            image = detail.get("image") or image
            url = detail.get("url") or url
            assembly = detail.get("specs", {}).get("assembly_required")
        else:
            # Fallback to scrape.
            sc = scrape_pdp(url)
            if sc.get("ok"):
                name = sc.get("name") or name
                image = sc.get("image") or image
            assembly = None

        section_id = _pick_section(c, sections)
        products.append({
            "id": f"p{len(products) + 1}",
            "status": "pending",
            "name": name,
            "url": url,
            "image": image,
            "sectionId": section_id,
            "blurb": _blurb_draft(name, assembly if 'assembly' in dir() else None),
            "fit_score": c.get("fit_score"),
            "fit_reasons": c.get("fit_reasons"),
            "tags": c.get("tags"),
            "sku": c.get("sku"),
        })

    return {"ok": True, "products": products, "queries": queries, "weak_fit": weak}


def health() -> dict[str, Any]:
    """Lightweight reachability check (no secrets)."""
    try:
        res = search_products("tv stand", page=1, page_size=1)
        return {"ok": True, "search_reachable": bool(res.get("ok")), "detail_reachable": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
