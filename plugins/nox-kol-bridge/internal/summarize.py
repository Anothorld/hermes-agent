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


def _format_region_distribution(audience: dict[str, Any]) -> Optional[str]:
    regions = audience.get("regions") or audience.get("region_distribution")
    if not isinstance(regions, list) or not regions:
        return None
    parts: list[str] = []
    for item in regions[:5]:
        if isinstance(item, dict):
            label = (
                item.get("country")
                or item.get("code")
                or item.get("name")
                or item.get("region")
            )
            pct = item.get("percent") or item.get("percentage") or item.get("ratio")
            if label and pct is not None:
                try:
                    p = float(pct)
                    pct_s = f"{p * 100:.1f}%" if p <= 1 else f"{p:.1f}%"
                except (TypeError, ValueError):
                    pct_s = str(pct)
                parts.append(f"{label} ({pct_s})")
            elif label:
                parts.append(str(label))
        elif isinstance(item, str) and item.strip():
            parts.append(item.strip())
    return ", ".join(parts) if parts else None


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
            name = item.get("tag") or item.get("name") or item.get("label")
            if name:
                tags.append(str(name))
    return ", ".join(tags) if tags else None


def _format_gender_skew(audience: dict[str, Any]) -> Optional[str]:
    for key in ("gender_distribution", "gender", "gender_ratio"):
        raw = audience.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            parts = []
            for g, v in raw.items():
                if v is None:
                    continue
                try:
                    p = float(v)
                    pct_s = f"{p * 100:.1f}%" if p <= 1 else f"{p:.1f}%"
                except (TypeError, ValueError):
                    pct_s = str(v)
                parts.append(f"{g} {pct_s}")
            if parts:
                return ", ".join(parts)
    female = audience.get("female_ratio") or audience.get("female_percent")
    if female is not None:
        try:
            p = float(female)
            pct_s = f"{p * 100:.1f}%" if p <= 1 else f"{p:.1f}%"
            return f"female {pct_s}"
        except (TypeError, ValueError):
            pass
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


def _format_interests(audience: dict[str, Any]) -> Optional[str]:
    for key in (
        "audience_interests",
        "interests",
        "interest_distribution",
        "top_interests",
    ):
        formatted = _format_tag_list(audience.get(key))
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
    nox_score_raw = profile.get("nox_score")
    nox_score = _unwrap_nox_metric(nox_score_raw)
    nox_score_breakdown = _extract_nox_score_breakdown(nox_score_raw)
    audience_auth = _unwrap_nox_metric(
        audience.get("audience_authenticity")
        or audience.get("authenticity_status")
    )

    verdict = _heuristic_verdict(profile, audience, content, cooperation)
    bench = profile.get("view_per_followers_benchmark")
    benchmark_rank = profile.get("benchmark_rank")
    if benchmark_rank is None and isinstance(bench, dict):
        benchmark_rank = bench.get("rank")
    dispute_count = None
    if cooperation:
        dispute_count = cooperation.get("dispute_count") or cooperation.get("disputes")
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
        "country": country,
        "nox_score": nox_score,
        "nox_score_breakdown": nox_score_breakdown,
        "platform": profile.get("platform") or profile.get("channel_platform"),
        "audience_top_regions": audience_top,
        "audience_authenticity": audience_auth,
        "audience_quality_score": audience_quality,
        "gender_skew": _format_gender_skew(audience),
        "audience_interests_top": _format_interests(audience),
        "content_tags_top": _format_tag_list(
            content.get("tags")
            or content.get("content_tags")
            or content.get("top_tags"),
        ),
        "benchmark_rank": benchmark_rank,
        "channel_url": profile.get("channel_url"),
        "dispute_count": dispute_count,
        "nox_diligence_verdict": verdict,
        "highlights": {
            "audience_authenticity": audience_auth,
            "benchmark_rank": benchmark_rank,
            "dispute_signal": dispute_count,
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
) -> str:
    """Four-level verdict aligned with Nox skill heuristics (simplified)."""
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
