"""Read-only access to POVISON product reviews in the magento2 database.

This module backs the Editorial Picks placement style: each editorial card can
quote one real, APPROVED buyer review (name + date + quote + star rating)
rather than a fabricated testimonial. It is the tooled, no-Agent-SQL surface
required by the workspace §4 guardrail (deterministic reads must go through a
tool, never ad-hoc SQL from the model).

Schema reference: ``/Users/arnold/Downloads/商品评论系统.md`` — a review spans
7 physical tables. We only READ; there is no write path here. Ratings are
stored on a 0–100 scale (``rating_sum``); a star rating is ``rating_sum / 20``.

Configuration (env, loaded from the seo-studio ``.env`` or the profile env):

    MAGENTO_DB_HOST  e.g. 35.233.185.233
    MAGENTO_DB_PORT  default 3306
    MAGENTO_DB_USER  e.g. admin
    MAGENTO_DB_PASS  the password
    MAGENTO_DB_NAME  default magento2
"""
from __future__ import annotations

import logging
import os
from typing import Any

import mysql.connector  # type: ignore

logger = logging.getLogger("povison_reviews")

# ── env config ────────────────────────────────────────────────────────────

_ENV_HOST = "MAGENTO_DB_HOST"
_ENV_PORT = "MAGENTO_DB_PORT"
_ENV_USER = "MAGENTO_DB_USER"
_ENV_PASS = "MAGENTO_DB_PASS"
_ENV_NAME = "MAGENTO_DB_NAME"

_DEFAULT_PORT = "3306"
_DEFAULT_NAME = "magento2"


def is_configured() -> bool:
    """True when the minimum env vars needed to attempt a DB connection are set.

    The password is the one truly required secret; host/user/name have sensible
    defaults but a missing password means we cannot authenticate, so we report
    not-configured and the Bridge endpoint returns ``ok=False`` instead of 500.
    """
    return bool(os.environ.get(_ENV_HOST) and os.environ.get(_ENV_PASS))


def _conn_kwargs() -> dict[str, Any]:
    """Build mysql.connector connect kwargs from env. Pure, no I/O."""
    return {
        "host": os.environ.get(_ENV_HOST, ""),
        "port": int(os.environ.get(_ENV_PORT, _DEFAULT_PORT) or _DEFAULT_PORT),
        "user": os.environ.get(_ENV_USER, ""),
        "password": os.environ.get(_ENV_PASS, ""),
        "database": os.environ.get(_ENV_NAME, _DEFAULT_NAME),
        "connection_timeout": 10,
    }


# ── SQL ────────────────────────────────────────────────────────────────────
#
# We join the review body (review_detail) with the photoreview rating table
# (magenest_photoreview_detail, holds rating_sum on the 0–100 scale) and the
# cross-SPU association (review_product, holds helpful_count). Only APPROVED
# reviews (review.status_id = 1) are exposed — that is the public-facing set
# (see 商品评论系统.md §3.1 / §5.4). Sorted by rating then helpfulness so the
# caller's "pick one good quote" heuristic gets the best material first.

_FETCH_REVIEWS_SQL = """
    SELECT
        r.review_id,
        rd.nickname,
        rd.title,
        rd.detail,
        DATE_FORMAT(r.created_at, '%Y-%m-%d') AS review_date,
        mpd.rating_sum,
        COALESCE(rp.helpful_count, 0) AS helpful_count,
        rd.source_type
    FROM review r
    JOIN review_detail rd          ON rd.review_id = r.review_id
    JOIN magenest_photoreview_detail mpd ON mpd.review_id = r.review_id
    LEFT JOIN review_product rp   ON rp.review_id = r.review_id
        AND rp.entity_pk_value = %s
    WHERE r.status_id = 1
      AND r.entity_pk_value = %s
      {rating_filter}
    ORDER BY mpd.rating_sum DESC, helpful_count DESC, r.created_at DESC
    LIMIT %s
""".strip()

_SUMMARY_SQL = """
    SELECT
        COALESCE(re.reviews_count, 0) AS reviews_count,
        COALESCE(re.rating_summary, 0) AS rating_summary
    FROM review_entity_summary re
    WHERE re.entity_pk_value = %s
""".strip()


def _rating_to_stars(rating_sum: Any) -> int | None:
    """Convert the stored 0–100 rating_sum to a 1–5 star integer.

    ``reviewRating = userRate * 20`` on submit (商品评论系统.md §2.3), so the
    inverse is ``rating_sum / 20``. Returns None on garbage/NULL.
    """
    try:
        v = int(rating_sum)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    stars = v // 20
    return max(1, min(5, stars or 1))


# ── public read API ───────────────────────────────────────────────────────


def fetch_reviews(
    spu: int | str,
    limit: int = 5,
    min_rating: int = 0,
) -> list[dict[str, Any]]:
    """Fetch APPROVED reviews for a product SPU, best-rated first.

    Args:
        spu: the magento product_id (SPU) the reviews are attached to.
        limit: cap on number of reviews returned (1–50).
        min_rating: optional floor in STAR units (1–5); 0 = no filter. Reviews
            below this star level are excluded.

    Returns:
        A list of review dicts: ``{reviewId, nickname, date, title, detail,
        rating, helpfulCount, sourceType}``. Empty list on no rows / not
        configured / DB error — never raises to the caller (logs at WARNING).
    """
    if not is_configured():
        logger.warning("povison_reviews.fetch_reviews: not configured (MAGENTO_DB_* missing)")
        return []
    try:
        spu_int = int(spu)
    except (TypeError, ValueError):
        logger.warning("povison_reviews.fetch_reviews: invalid spu %r", spu)
        return []
    limit = max(1, min(50, int(limit)))
    min_rating = max(0, min(5, int(min_rating)))

    rating_filter = ""
    if min_rating > 0:
        # min_rating is in STAR units; the column is 0–100 → multiply by 20.
        rating_filter = f"AND mpd.rating_sum >= {int(min_rating) * 20}"

    sql = _FETCH_REVIEWS_SQL.format(rating_filter=rating_filter)
    try:
        with mysql.connector.connect(**_conn_kwargs()) as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(sql, (spu_int, spu_int, limit))
                rows = cur.fetchall()
    except mysql.connector.Error as exc:
        logger.warning("povison_reviews.fetch_reviews DB error for spu=%s: %s", spu_int, exc)
        return []
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("povison_reviews.fetch_reviews unexpected error for spu=%s: %s", spu_int, exc)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "reviewId": row.get("review_id"),
                "nickname": row.get("nickname") or "",
                "date": row.get("review_date") or "",
                "title": row.get("title") or "",
                "detail": row.get("detail") or "",
                "rating": _rating_to_stars(row.get("rating_sum")),
                "helpfulCount": int(row.get("helpful_count") or 0),
                "sourceType": row.get("source_type") or "",
            }
        )
    return out


def fetch_summary(spu: int | str) -> dict[str, Any]:
    """Fetch the pre-aggregated review count + average rating for a SPU.

    Reads from ``review_entity_summary`` (the CMS job-maintained aggregate) so
    we don't pay a full COUNT/AVG on every call. ``ratingSummary`` is on the
    0–100 scale; the returned ``rating`` field is converted to stars.

    Returns:
        ``{reviewsCount, ratingSummary, rating}`` where ``rating`` is 1–5 stars
        or None when no aggregate exists. Never raises.
    """
    if not is_configured():
        logger.warning("povison_reviews.fetch_summary: not configured (MAGENTO_DB_* missing)")
        return {"reviewsCount": 0, "ratingSummary": 0, "rating": None}
    try:
        spu_int = int(spu)
    except (TypeError, ValueError):
        logger.warning("povison_reviews.fetch_summary: invalid spu %r", spu)
        return {"reviewsCount": 0, "ratingSummary": 0, "rating": None}

    try:
        with mysql.connector.connect(**_conn_kwargs()) as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(_SUMMARY_SQL, (spu_int,))
                row = cur.fetchone() or {}
    except mysql.connector.Error as exc:
        logger.warning("povison_reviews.fetch_summary DB error for spu=%s: %s", spu_int, exc)
        return {"reviewsCount": 0, "ratingSummary": 0, "rating": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("povison_reviews.fetch_summary unexpected error for spu=%s: %s", spu_int, exc)
        return {"reviewsCount": 0, "ratingSummary": 0, "rating": None}

    reviews_count = int(row.get("reviews_count") or 0)
    rating_summary = int(row.get("rating_summary") or 0)
    return {
        "reviewsCount": reviews_count,
        "ratingSummary": rating_summary,
        "rating": _rating_to_stars(rating_summary) if reviews_count else None,
    }


# ── product URL → magento entity_id (SPU) resolution ────────────────────────
#
# Editorial Picks products carry a povison storefront URL (e.g.
# https://www.povison.com/<slug>.html?variant=...) but the magento2 review DB
# keys reviews by the numeric ``entity_id`` (stored as ``review.entity_pk_value``
# and matched to the product via ``catalog_product_entity_varchar.url_key``).
# Neither the storefront handle id (``pfavrd3``) nor the catalog API SPU
# (``M2-TS8125``) is that numeric id, so the Agent cannot call fetch_reviews
# directly with the product's ``id``/``spu``. This resolver bridges the gap:
# slug → url_key attribute → catalog_product_entity_varchar → entity_id.

_URL_KEY_SQL = """
    SELECT cpev.entity_id
    FROM catalog_product_entity_varchar cpev
    JOIN eav_attribute ea ON ea.attribute_id = cpev.attribute_id
        AND ea.attribute_code = 'url_key'
        AND ea.entity_type_id = (
            SELECT entity_type_id FROM eav_entity_type
            WHERE entity_type_code = 'catalog_product'
        )
    WHERE cpev.value = %s
    LIMIT 1
""".strip()


def _slug_from_url(url: str) -> str | None:
    """Extract the magento url_key slug from a povison product URL.

    Handles ``https://www.povison.com/<slug>.html?variant=...`` and bare
    ``/<slug>.html`` paths. Returns the slug without the ``.html`` suffix, or
    None when no slug can be extracted.
    """
    if not url:
        return None
    from urllib.parse import urlsplit

    path = urlsplit(url).path
    if not path:
        return None
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    if slug.lower().endswith(".html"):
        slug = slug[:-5]
    return slug or None


def resolve_spu_by_url(url: str) -> int | None:
    """Resolve a povison product URL to its magento numeric entity_id (SPU).

    Reads ``catalog_product_entity_varchar`` (url_key attribute) so the Agent
    can fetch reviews without already knowing the numeric id. Returns the
    entity_id, or None when not configured / no match / DB error. Never raises.
    """
    if not is_configured():
        logger.warning("povison_reviews.resolve_spu_by_url: not configured (MAGENTO_DB_* missing)")
        return None
    slug = _slug_from_url(url)
    if not slug:
        logger.warning("povison_reviews.resolve_spu_by_url: no slug in url %r", url)
        return None
    try:
        with mysql.connector.connect(**_conn_kwargs()) as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute(_URL_KEY_SQL, (slug,))
                row = cur.fetchone()
    except mysql.connector.Error as exc:
        logger.warning("povison_reviews.resolve_spu_by_url DB error for slug=%s: %s", slug, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("povison_reviews.resolve_spu_by_url unexpected error for slug=%s: %s", slug, exc)
        return None
    if not row:
        return None
    try:
        return int(row.get("entity_id"))
    except (TypeError, ValueError):
        return None
