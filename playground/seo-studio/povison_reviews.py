"""POVISON product reviews from the magento2 database.

Read-only access to APPROVED customer reviews for the Editorial Picks blog
placement style. One review = 7 physical tables inherited from Magento's
Magenest PhotoReview plugin (see ``商品评论系统.md``):

  review (主体)
    ├── review_detail (title / detail / nickname / email)
    ├── magenest_photoreview_detail (ratingSum 百分制 / isRecommend)
    ├── rating_option_vote (per-dimension vote)
    ├── magenest_photoreview_photo (images / videos)
    ├── review_product (SPU 关联 + helpfulCount)
    ├── review_sku (SKU 关联)
    └── review_store (站点关联)

Pre-aggregated counts live in ``review_entity_summary`` (reviewsCount +
ratingSummary, kept fresh by a CMS XXL-Job).

Scoring: ``ratingSum`` is stored on a 0-100 scale; divide by 20 for the
1-5 star value shown on the storefront (``EditComment.vue:275`` /
``CommentList.vue:408``).

Credentials come from environment variables (loaded by ``server._load_dotenv``
or the operator's shell). Only read queries are issued — no writes.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["health", "fetch_reviews", "summary", "resolve_spu"]


# ── env-driven connection config ──────────────────────────────────────────

def _db_config() -> dict[str, Any]:
    """Return MySQL connection kwargs from SEO_STUDIO_REVIEWS_MYSQL_* env."""
    return {
        "host": os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_HOST", ""),
        "port": int(os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_PORT", "3306") or "3306"),
        "user": os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_USER", ""),
        "password": os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_PASS", "") or os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_PASSWORD", ""),
        "database": os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_DB", "magento2") or "magento2",
        "connect_timeout": int(os.environ.get("SEO_STUDIO_REVIEWS_MYSQL_TIMEOUT", "8") or "8"),
    }


def _connect():
    """Create a pymysql connection. Raises ImportError if pymysql missing."""
    import pymysql  # type: ignore
    cfg = _db_config()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError(
            "reviews DB not configured — set SEO_STUDIO_REVIEWS_MYSQL_HOST / "
            "SEO_STUDIO_REVIEWS_MYSQL_USER / SEO_STUDIO_REVIEWS_MYSQL_PASS"
        )
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        connect_timeout=cfg["connect_timeout"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── public API ─────────────────────────────────────────────────────────────

def health() -> dict[str, Any]:
    """Report reviews DB reachability. Never raises."""
    try:
        import pymysql  # type: ignore  # noqa: F401
    except ImportError:
        return {"ok": False, "error": "pymysql not installed (pip install pymysql)"}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"ok": True, "db": _db_config()["database"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def resolve_spu(variant: str | None = None, product_url: str | None = None) -> str | None:
    """Resolve an SPU id from a variant id or PDP URL.

    The reviews system keys on SPU (``review_product.product_id``). The catalog
    Detail API returns ``variant_id``; we look it up via ``review_sku`` when an
    SPU isn't directly available. Returns the SPU id as a string, or None.
    """
    if not variant and product_url:
        # best-effort parse of ?variant=<id> from the PDP URL
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(product_url).query)
        variant = (q.get("variant") or [None])[0]
    if not variant:
        return None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_id FROM review_sku WHERE sku_id = %s LIMIT 1",
                    (variant,),
                )
                row = cur.fetchone()
                return str(row["product_id"]) if row else None
    except Exception:
        return None


def fetch_reviews(
    spu: str,
    limit: int = 5,
    min_rating: int = 0,
) -> dict[str, Any]:
    """Fetch APPROVED reviews for an SPU, sorted by rating + helpfulness.

    Args:
        spu: the product SPU id (``review_product.product_id``).
        limit: max reviews to return (1-20, clamped).
        min_rating: only return reviews with at least this star rating (0-5).

    Returns ``{ok, spu, reviews: [{reviewId, nickname, date, title, detail,
    rating, helpfulCount, sourceType}], count}``. Never raises — errors land
    in ``ok: False``.
    """
    limit = max(1, min(20, int(limit)))
    min_rating_pct = max(0, min(5, int(min_rating))) * 20
    try:
        import pymysql  # type: ignore
    except ImportError:
        return {"ok": False, "error": "pymysql not installed (pip install pymysql)"}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                # review.status_id = 1 → APPROVED (see 商品评论系统.md §3.1)
                # ratingSum is 0-100; /20 for 1-5 stars.
                cur.execute(
                    """
                    SELECT
                        r.review_id           AS reviewId,
                        rd.nickname           AS nickname,
                        DATE(r.display_time)  AS date,
                        rd.title             AS title,
                        rd.detail            AS detail,
                        mpd.rating_sum       AS ratingSum,
                        rp.helpful_count     AS helpfulCount,
                        rd.source_type       AS sourceType
                    FROM review r
                    JOIN review_detail rd
                        ON rd.review_id = r.review_id
                    JOIN magenest_photoreview_detail mpd
                        ON mpd.review_id = r.review_id
                    LEFT JOIN review_product rp
                        ON rp.review_id = r.review_id
                        AND rp.product_id = %s
                    WHERE r.status_id = 1
                      AND (rp.product_id = %s)
                      AND mpd.rating_sum >= %s
                    ORDER BY mpd.rating_sum DESC, rp.helpful_count DESC, r.display_time DESC
                    LIMIT %s
                    """,
                    (spu, spu, min_rating_pct, limit),
                )
                rows = cur.fetchall()
        reviews = []
        for row in rows:
            rating_sum = row.get("ratingSum") or 0
            date_val = row.get("date")
            reviews.append({
                "reviewId": row.get("reviewId"),
                "nickname": (row.get("nickname") or "").strip(),
                "date": date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val) if date_val else "",
                "title": (row.get("title") or "").strip(),
                "detail": (row.get("detail") or "").strip(),
                "rating": round(rating_sum / 20) if rating_sum else 0,
                "helpfulCount": row.get("helpfulCount") or 0,
                "sourceType": (row.get("sourceType") or "").strip(),
            })
        return {"ok": True, "spu": str(spu), "reviews": reviews, "count": len(reviews)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def summary(spu: str) -> dict[str, Any]:
    """Fetch the pre-aggregated review summary for an SPU.

    Reads from ``review_entity_summary`` (kept fresh by the CMS
    ``ReviewStatisticsJob`` — see 商品评论系统.md §7.2). Returns
    ``{ok, spu, reviewsCount, ratingSummary, rating(星)}``.
    """
    try:
        import pymysql  # type: ignore
    except ImportError:
        return {"ok": False, "error": "pymysql not installed (pip install pymysql)"}
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT reviews_count, rating_summary
                    FROM review_entity_summary
                    WHERE entity_pk_value = %s
                    LIMIT 1
                    """,
                    (spu,),
                )
                row = cur.fetchone()
        if not row:
            return {"ok": True, "spu": str(spu), "reviewsCount": 0, "ratingSummary": 0, "rating": 0}
        rs = row.get("rating_summary") or 0
        return {
            "ok": True,
            "spu": str(spu),
            "reviewsCount": row.get("reviews_count") or 0,
            "ratingSummary": rs,
            "rating": round(rs / 20, 1) if rs else 0,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
