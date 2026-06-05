"""Build normalized_summary from Nox API envelopes for CAL hydrate."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional


_NOX_SCORE_DIM_KEYS = (
    "overall",
    "growth",
    "creativity",
    "audience",
    "engagement",
    "credibility",
)


def _extract_nox_score_breakdown(raw: Any) -> Optional[dict[str, Any]]:
    """Preserve full Nox score object for operator dashboard."""
    if not isinstance(raw, dict) or "overall" not in raw:
        return None
    out: dict[str, Any] = {}
    for key in _NOX_SCORE_DIM_KEYS:
        if key in raw and raw[key] is not None:
            out[key] = raw[key]
    return out or None


def _unwrap_nox_metric(raw: Any) -> Any:
    """Nox often returns ``{value, status, ...}`` or ``{overall, growth, ...}``."""
    if isinstance(raw, dict):
        if "value" in raw:
            return raw.get("value")
        if "score" in raw:
            return raw.get("score")
        if "overall" in raw:
            return raw.get("overall")
    return raw


_AUDIENCE_TYPE_LABELS = {
    "usualUser": "真实用户",
    "suspicious": "可疑账号",
    "generator": "机器人",
    "influencer": "达人粉丝",
    "inactive": "不活跃",
    "real": "真实",
}


def _format_pct_value(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    return f"{p * 100:.1f}%" if p <= 1 else f"{p:.1f}%"


def _format_name_value_distribution(
    raw: Any,
    *,
    limit: int = 8,
    label_map: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Format Nox ``[{name, value}, ...]`` arrays (regions, genders, ages, etc.)."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if not isinstance(raw, list):
        return None
    parts: list[str] = []
    for item in raw[:limit]:
        if isinstance(item, dict):
            label = (
                item.get("name")
                or item.get("tag")
                or item.get("label")
                or item.get("country")
                or item.get("code")
                or item.get("region")
            )
            if not label:
                continue
            display_label = label_map.get(str(label), str(label)) if label_map else str(label)
            pct_raw = (
                item.get("value")
                or item.get("percent")
                or item.get("percentage")
                or item.get("ratio")
            )
            if pct_raw is not None:
                pct_s = _format_pct_value(pct_raw)
                parts.append(f"{display_label} ({pct_s})" if pct_s else str(display_label))
            else:
                parts.append(display_label)
        elif isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return ", ".join(parts) if parts else None


def _format_region_distribution(audience: dict[str, Any]) -> Optional[str]:
    regions = audience.get("regions") or audience.get("region_distribution")
    return _format_name_value_distribution(regions, limit=5)


def _format_tag_list(raw: Any, *, limit: int = 8) -> Optional[str]:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if not isinstance(raw, list):
        return None
    tags: list[str] = []
    for item in raw[:limit]:
        if isinstance(item, str) and item.strip():
            tags.append(item.strip())
        elif isinstance(item, dict):
            name = (
                item.get("tag")
                or item.get("name")
                or item.get("label")
                or item.get("keyword")
            )
            if name:
                tags.append(str(name))
    return ", ".join(tags) if tags else None


def _format_level_summary(profile: dict[str, Any]) -> Optional[str]:
    specs = (
        ("avg_views_level", "播放"),
        ("wave_level", "波动"),
        ("engagement_rate_level", "互动"),
        ("video_count_level", "视频量"),
        ("view_per_followers_level", "粉播比"),
    )
    parts: list[str] = []
    for key, label in specs:
        val = profile.get(key)
        if val is not None and val != "":
            parts.append(f"{label} L{val}")
    return " · ".join(parts) if parts else None


def _format_benchmark_ranks(profile: dict[str, Any]) -> Optional[str]:
    specs = (
        ("avg_views_benchmark", "播放"),
        ("wave_benchmark", "波动"),
        ("engagement_rate_benchmark", "互动"),
        ("view_per_followers_benchmark", "粉播比"),
    )
    parts: list[str] = []
    for key, label in specs:
        bench = profile.get(key)
        if isinstance(bench, dict) and bench.get("rank") is not None:
            try:
                rank = float(bench["rank"])
                parts.append(f"{label} {rank * 100:.0f}%")
            except (TypeError, ValueError):
                parts.append(f"{label} {bench['rank']}")
    return " · ".join(parts) if parts else None


def _format_content_format_counts(profile: dict[str, Any]) -> Optional[str]:
    specs = (
        ("posts_count", "帖"),
        ("reels_count", "Reels"),
        ("pics_count", "图"),
        ("video_count", "视频"),
        ("normal_count", "长视频"),
        ("shorts_count", "短视频"),
    )
    parts: list[str] = []
    for key, label in specs:
        val = profile.get(key)
        if val is not None and val != "":
            parts.append(f"{label} {val}")
    return " · ".join(parts) if parts else None


def _format_content_engagement_split(profile: dict[str, Any]) -> Optional[str]:
    specs = (
        ("avg_engagement_reels", "Reels"),
        ("avg_engagement_pics", "图片"),
        ("avg_engagement_normal", "长视频"),
        ("avg_engagement_shorts", "短视频"),
    )
    parts: list[str] = []
    for key, label in specs:
        val = profile.get(key)
        if val is not None and val != "":
            parts.append(f"{label} {val}")
    return " · ".join(parts) if parts else None


def _format_string_list(raw: Any, *, limit: int = 5) -> Optional[str]:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if not isinstance(raw, list) or not raw:
        return None
    parts: list[str] = []
    for item in raw[:limit]:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif item is not None:
            parts.append(str(item))
    return ", ".join(parts) if parts else None


def _format_authenticity_range(audience: dict[str, Any]) -> Optional[str]:
    raw = audience.get("audience_authenticity") or audience.get("authenticity_status")
    if not isinstance(raw, dict):
        return None
    vmin = raw.get("ratio_min")
    vmax = raw.get("ratio_max")
    if vmin is None or vmax is None:
        return None
    lo = _format_pct_value(vmin)
    hi = _format_pct_value(vmax)
    if lo and hi:
        return f"{lo}–{hi}"
    return None


def _resolve_platform(
    profile: dict[str, Any],
    audience: dict[str, Any],
    content: dict[str, Any],
) -> Optional[str]:
    for src in (profile, audience, content):
        platform = src.get("platform") or src.get("channel_platform")
        if platform:
            return str(platform)
    social = profile.get("social_media")
    if isinstance(social, list):
        for item in social:
            if isinstance(item, dict) and item.get("platform"):
                return str(item["platform"])
    return None


def _resolve_channel_handle(
    profile: dict[str, Any],
    audience: dict[str, Any],
    content: dict[str, Any],
) -> Optional[str]:
    for src in (profile, audience, content):
        handle = src.get("channel_handle")
        if handle:
            return str(handle)
    return None


def _format_usd_amount(raw: Any) -> Optional[str]:
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if n >= 1000:
        return f"${n:,.0f}"
    return f"${n:.0f}"


def _format_price_band(
    cooperation: dict[str, Any],
    *,
    prefix: str,
) -> Optional[str]:
    lo = cooperation.get(f"{prefix}_min")
    hi = cooperation.get(f"{prefix}_max")
    avg_key = f"{prefix}_avg" if prefix == "estimated_price" else None
    avg = cooperation.get(avg_key) if avg_key else None
    lo_s = _format_usd_amount(lo)
    hi_s = _format_usd_amount(hi)
    if lo_s and hi_s:
        band = f"{lo_s}–{hi_s}"
        avg_s = _format_usd_amount(avg)
        if avg_s and prefix == "estimated_price":
            return f"{band}（均 {avg_s}）"
        return band
    return _format_usd_amount(avg)


def _format_brands_top(cooperation: dict[str, Any], *, limit: int = 6) -> Optional[str]:
    brands = cooperation.get("brands") or cooperation.get("brand_partnerships")
    if not isinstance(brands, list) or not brands:
        return None
    parts: list[str] = []
    for item in brands[:limit]:
        if not isinstance(item, dict):
            continue
        name = item.get("brand_name") or item.get("name")
        if not name:
            continue
        vc = item.get("video_count")
        er = item.get("engagement_rate")
        label = str(name)
        extras: list[str] = []
        if vc is not None:
            extras.append(f"{vc}支")
        if er is not None:
            pct = _format_pct_value(er)
            if pct:
                extras.append(pct)
        if extras:
            label = f"{label} ({', '.join(extras)})"
        parts.append(label)
    return ", ".join(parts) if parts else None


def _format_active_period(cooperation: dict[str, Any]) -> Optional[str]:
    lo = cooperation.get("active_period_min")
    hi = cooperation.get("active_period_max")
    if lo and hi:
        return f"{lo}–{hi}"
    return str(lo or hi) if (lo or hi) else None


def _resolve_cooperation_signals(
    profile: dict[str, Any],
    cooperation: dict[str, Any],
) -> dict[str, Any]:
    """Merge cooperation fields from profile (IG detail) and cooperation dimension."""
    src = {**profile, **cooperation}
    dispute_count = cooperation.get("dispute_count") or cooperation.get("disputes")
    if dispute_count is None:
        types = src.get("dispute_types")
        if isinstance(types, list) and types:
            dispute_count = len(types)
    contact_parts: list[str] = []
    for key, label in (
        ("avg_contact_days", "联系"),
        ("avg_contact_chats", "轮对话"),
        ("avg_collaboration_days", "合作周期"),
    ):
        val = src.get(key)
        if val is not None and val != "":
            contact_parts.append(f"{label} {val}")
    ad_parts: list[str] = []
    ad_pct = src.get("ad_video_percent")
    if ad_pct is not None:
        pct = _format_pct_value(ad_pct)
        if pct:
            ad_parts.append(f"占比 {pct}")
    ad_month = src.get("ad_video_count_per_month")
    if ad_month is not None:
        ad_parts.append(f"月均 {ad_month}")
    ad_views = src.get("ad_video_avg_views")
    if ad_views is not None:
        ad_parts.append(f"均播 {int(float(ad_views)):,}")
    return {
        "cooperation_score": _unwrap_nox_metric(
            cooperation.get("cooperation_score") or profile.get("cooperation_score")
        ),
        "cooperation_pros": _format_string_list(
            cooperation.get("cooperation_pros") or profile.get("cooperation_pros")
        ),
        "cooperation_cons": _format_string_list(
            cooperation.get("cooperation_cons") or profile.get("cooperation_cons")
        ),
        "dispute_types": _format_string_list(
            cooperation.get("dispute_types") or profile.get("dispute_types")
        ),
        "dispute_count": dispute_count,
        "cooperation_price_estimate": _format_price_band(src, prefix="estimated_price"),
        "cooperation_first_price_range": _format_price_band(src, prefix="first_price"),
        "cooperation_final_price_range": _format_price_band(src, prefix="final_price"),
        "cooperation_avg_response_hours": src.get("avg_response_hours"),
        "cooperation_contact_efficiency": (
            " · ".join(contact_parts) if contact_parts else None
        ),
        "cooperation_ad_video_stats": " · ".join(ad_parts) if ad_parts else None,
        "cooperation_brands_top": _format_brands_top(src),
        "cooperation_confirmation_pct": _unwrap_nox_metric(
            src.get("cooperation_confirmation_pct")
        ),
        "cooperation_start_contact_pct": _unwrap_nox_metric(
            src.get("start_contact_pct")
        ),
        "cooperation_promotion_online_pct": _unwrap_nox_metric(
            src.get("promotion_online_pct")
        ),
        "cooperation_active_period": _format_active_period(src),
        "cooperation_brand_video_engagement_rate": src.get(
            "brand_video_engagement_rate"
        ),
    }


def _format_gender_skew(audience: dict[str, Any]) -> Optional[str]:
    genders = _format_name_value_distribution(audience.get("genders"), limit=4)
    if genders:
        return genders
    for key in ("gender_distribution", "gender", "gender_ratio"):
        raw = audience.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            parts = []
            for g, v in raw.items():
                if v is None:
                    continue
                pct_s = _format_pct_value(v)
                if pct_s:
                    parts.append(f"{g} {pct_s}")
            if parts:
                return ", ".join(parts)
    female = audience.get("female_ratio") or audience.get("female_percent")
    if female is not None:
        pct_s = _format_pct_value(female)
        if pct_s:
            return f"female {pct_s}"
    return None


def _format_age_distribution(audience: dict[str, Any]) -> Optional[str]:
    for key in ("follower_ages", "ages", "age_distribution"):
        formatted = _format_name_value_distribution(audience.get(key), limit=8)
        if formatted:
            return formatted
    female = _format_name_value_distribution(audience.get("female_ages"), limit=7)
    male = _format_name_value_distribution(audience.get("male_ages"), limit=7)
    parts: list[str] = []
    if female:
        parts.append(f"女 {female}")
    if male:
        parts.append(f"男 {male}")
    return " · ".join(parts) if parts else None


def _format_languages(audience: dict[str, Any]) -> Optional[str]:
    for key in ("languages", "language_distribution", "follower_languages"):
        formatted = _format_name_value_distribution(audience.get(key), limit=6)
        if formatted:
            return formatted
    return None


def _format_audience_types(audience: dict[str, Any]) -> Optional[str]:
    for key in ("audience_types", "audience_type_distribution"):
        formatted = _format_name_value_distribution(
            audience.get(key),
            limit=6,
            label_map=_AUDIENCE_TYPE_LABELS,
        )
        if formatted:
            return formatted
    return None


def _format_adults_split(audience: dict[str, Any]) -> Optional[str]:
    return _format_name_value_distribution(audience.get("adults"), limit=4)


def _format_scalar_audience_metric(audience: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        raw = audience.get(key)
        if raw is None or raw == "":
            continue
        return _unwrap_nox_metric(raw)
    return None


def _infer_followers_from_views_ratio(profile: dict[str, Any]) -> Optional[int]:
    """Instagram profile often omits ``followers``; Nox exposes views/followers ratio."""
    ratio = profile.get("view_per_followers")
    if ratio is None:
        return None
    try:
        r = float(ratio)
        if r <= 0:
            return None
    except (TypeError, ValueError):
        return None
    for key in ("avg_views", "median_views"):
        views = profile.get(key)
        if views is None:
            continue
        try:
            v = float(views)
            if v > 0:
                return int(round(v / r))
        except (TypeError, ValueError):
            continue
    return None


def _resolve_followers(
    profile: dict[str, Any],
    audience: dict[str, Any],
    content: dict[str, Any],
) -> tuple[Any, Optional[str]]:
    """Return (followers, source_tag). source_tag explains provenance for UI."""
    for src in (profile, audience, content):
        for key in (
            "followers",
            "subscriber_count",
            "fans",
            "fan_count",
            "followers_count",
        ):
            v = src.get(key)
            if v is not None and v != "":
                return v, "nox_api"
    social = _followers_from_social(profile)
    if social is not None:
        return social, "nox_social_media"
    inferred = _infer_followers_from_views_ratio(profile)
    if inferred is not None:
        return inferred, "inferred_views_ratio"
    return None, None


def _format_interests(
    audience: dict[str, Any],
    content: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    for src in (audience, content or {}):
        for key in (
            "audience_interests",
            "interests",
            "interest_distribution",
            "top_interests",
        ):
            formatted = _format_tag_list(src.get(key))
            if formatted:
                return formatted
    return None


def summarize_diligence_pack(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Extract fields for identity facts from diligence bundle."""
    profile = _data(bundle.get("profile"))
    audience = _data(bundle.get("audience"))
    content = _data(bundle.get("content"))
    cooperation = _data(bundle.get("cooperation"))

    creator_id = (
        profile.get("creator_id")
        or audience.get("creator_id")
        or content.get("creator_id")
    )
    creator_name = (
        profile.get("creator_name")
        or profile.get("nickname")
        or audience.get("creator_name")
    )
    followers, followers_source = _resolve_followers(profile, audience, content)
    engagement = (
        profile.get("engagement_rate")
        or content.get("engagement_rate")
        or profile.get("avg_engagement")
    )
    country = profile.get("country") or _top_region(audience)
    avg_views = profile.get("avg_views") or content.get("avg_views")
    median_views = profile.get("median_views") or content.get("median_views")
    nox_score_raw = profile.get("nox_score")
    nox_score = _unwrap_nox_metric(nox_score_raw)
    nox_score_breakdown = _extract_nox_score_breakdown(nox_score_raw)
    audience_auth = _unwrap_nox_metric(
        audience.get("audience_authenticity")
        or audience.get("authenticity_status")
    )
    coop = _resolve_cooperation_signals(profile, cooperation)

    verdict = _heuristic_verdict(
        profile,
        audience,
        content,
        cooperation,
        dispute_count=coop["dispute_count"],
    )
    bench = profile.get("view_per_followers_benchmark")
    benchmark_rank = profile.get("benchmark_rank")
    if benchmark_rank is None and isinstance(bench, dict):
        benchmark_rank = bench.get("rank")
    audience_top = _format_region_distribution(audience)
    audience_quality = _unwrap_nox_metric(
        audience.get("audience_quality")
        or audience.get("audience_quality_score")
        or audience.get("quality_score")
    )

    return {
        "nox_creator_id": creator_id,
        "creator_name": creator_name,
        "followers": followers,
        "followers_source": followers_source,
        "engagement_rate": engagement,
        "avg_views": avg_views,
        "median_views": median_views,
        "wave": profile.get("wave"),
        "avg_active_days": profile.get("avg_active_days"),
        "view_per_followers": profile.get("view_per_followers"),
        "performance_levels": _format_level_summary(profile),
        "benchmark_ranks": _format_benchmark_ranks(profile),
        "country": country,
        "nox_score": nox_score,
        "nox_score_breakdown": nox_score_breakdown,
        "platform": _resolve_platform(profile, audience, content),
        "channel_handle": _resolve_channel_handle(profile, audience, content),
        "audience_top_regions": audience_top,
        "audience_authenticity": audience_auth,
        "audience_authenticity_range": _format_authenticity_range(audience),
        "audience_quality_score": audience_quality,
        "gender_skew": _format_gender_skew(audience),
        "audience_age_distribution": _format_age_distribution(audience),
        "audience_languages_top": _format_languages(audience),
        "audience_types_top": _format_audience_types(audience),
        "audience_adults_split": _format_adults_split(audience),
        "audience_positive_pct": _format_scalar_audience_metric(
            audience, "positive_audience_pct"
        ),
        "audience_promo_attractiveness": _format_scalar_audience_metric(
            audience, "promo_attractiveness"
        ),
        "audience_promo_interested_pct": _format_scalar_audience_metric(
            audience, "promo_interested_audience_pct"
        ),
        "audience_promo_professionalism": _format_scalar_audience_metric(
            audience, "promo_professionalism"
        ),
        "audience_interests_top": _format_interests(audience, content),
        "content_tags_top": _format_tag_list(
            content.get("tags")
            or content.get("content_tags")
            or content.get("top_tags"),
        ),
        "content_tags_all": _format_tag_list(content.get("all_tags"), limit=12),
        "content_format_counts": _format_content_format_counts(profile),
        "content_engagement_split": _format_content_engagement_split(profile),
        "benchmark_rank": benchmark_rank,
        "channel_url": profile.get("channel_url"),
        "cooperation_score": coop["cooperation_score"],
        "cooperation_pros": coop["cooperation_pros"],
        "cooperation_cons": coop["cooperation_cons"],
        "dispute_types": coop["dispute_types"],
        "dispute_count": coop["dispute_count"],
        "cooperation_price_estimate": coop["cooperation_price_estimate"],
        "cooperation_first_price_range": coop["cooperation_first_price_range"],
        "cooperation_final_price_range": coop["cooperation_final_price_range"],
        "cooperation_avg_response_hours": coop["cooperation_avg_response_hours"],
        "cooperation_contact_efficiency": coop["cooperation_contact_efficiency"],
        "cooperation_ad_video_stats": coop["cooperation_ad_video_stats"],
        "cooperation_brands_top": coop["cooperation_brands_top"],
        "cooperation_confirmation_pct": coop["cooperation_confirmation_pct"],
        "cooperation_start_contact_pct": coop["cooperation_start_contact_pct"],
        "cooperation_promotion_online_pct": coop["cooperation_promotion_online_pct"],
        "cooperation_active_period": coop["cooperation_active_period"],
        "cooperation_brand_video_engagement_rate": coop[
            "cooperation_brand_video_engagement_rate"
        ],
        "nox_diligence_verdict": verdict,
        "highlights": {
            "audience_authenticity": audience_auth,
            "benchmark_rank": benchmark_rank,
            "dispute_signal": coop["dispute_count"],
            "channel_url": profile.get("channel_url"),
        },
    }


def summarize_contacts(envelope: Mapping[str, Any]) -> dict[str, Any]:
    data = _data(envelope)
    email = data.get("email")
    quality = data.get("email_quality")
    return {
        "email": email,
        "email_quality": quality,
        "nox_creator_id": data.get("creator_id"),
    }


def _followers_from_social(profile: dict[str, Any]) -> Any:
    social = profile.get("social_media")
    if not isinstance(social, list):
        return None
    for item in social:
        if not isinstance(item, dict):
            continue
        for key in ("followers", "subscriber_count", "fans"):
            if item.get(key) is not None:
                return item[key]
    return None


def _top_region(audience: dict[str, Any]) -> Optional[str]:
    regions = audience.get("regions")
    if isinstance(regions, list) and regions:
        first = regions[0]
        if isinstance(first, dict):
            return first.get("country") or first.get("code") or first.get("name")
        if isinstance(first, str):
            return first
    return None


def _data(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("success") is False:
        return {}
    data = envelope.get("data")
    return data if isinstance(data, dict) else {}


def _heuristic_verdict(
    profile: dict,
    audience: dict,
    content: dict,
    cooperation: dict,
    *,
    dispute_count: Any = None,
) -> str:
    """Four-level verdict aligned with Nox skill heuristics (simplified)."""
    disputes = dispute_count
    if disputes is None:
        disputes = cooperation.get("dispute_count") or cooperation.get("disputes")
    if disputes and int(disputes) > 0:
        return "not_priority"
    auth = _unwrap_nox_metric(
        audience.get("authenticity_status") or audience.get("audience_authenticity")
    )
    if isinstance(auth, str):
        auth_l = auth.lower()
    elif isinstance(auth, (int, float)):
        try:
            if float(auth) < 0.5:
                return "needs_manual_review"
        except (TypeError, ValueError):
            pass
        auth_l = ""
    else:
        auth_l = str(auth).lower() if auth else ""
    if auth_l in ("low", "suspicious", "poor"):
        return "needs_manual_review"
    rank = profile.get("benchmark_rank") or profile.get("percentile")
    bench = profile.get("view_per_followers_benchmark")
    if rank is None and isinstance(bench, dict):
        rank = bench.get("rank")
    if rank is not None:
        try:
            if float(rank) < 0.25:
                return "viable_with_risks"
        except (TypeError, ValueError):
            pass
    if (profile.get("engagement_rate") or profile.get("avg_views")) and (
        content.get("engagement_rate") or profile.get("engagement_rate")
    ):
        return "high_priority"
    return "needs_manual_review"


__all__ = ["summarize_contacts", "summarize_diligence_pack"]
