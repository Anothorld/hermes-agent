"""Build normalized_summary from Nox API envelopes for CAL hydrate."""

from __future__ import annotations

from typing import Any, Mapping, Optional


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
    followers = (
        profile.get("followers")
        or profile.get("subscriber_count")
        or audience.get("followers")
        or _followers_from_social(profile)
    )
    engagement = (
        profile.get("engagement_rate")
        or content.get("engagement_rate")
        or profile.get("avg_engagement")
    )
    country = profile.get("country") or _top_region(audience)
    avg_views = profile.get("avg_views") or content.get("avg_views")
    nox_score = profile.get("nox_score")
    audience_auth = (
        audience.get("audience_authenticity")
        or audience.get("authenticity_status")
        or audience.get("audience_quality")
    )

    verdict = _heuristic_verdict(profile, audience, content, cooperation)

    return {
        "nox_creator_id": creator_id,
        "creator_name": creator_name,
        "followers": followers,
        "engagement_rate": engagement,
        "avg_views": avg_views,
        "country": country,
        "nox_score": nox_score,
        "nox_diligence_verdict": verdict,
        "highlights": {
            "audience_authenticity": audience_auth,
            "benchmark_rank": profile.get("benchmark_rank")
            or profile.get("view_per_followers_benchmark", {}).get("rank")
            if isinstance(profile.get("view_per_followers_benchmark"), dict)
            else profile.get("benchmark_rank"),
            "dispute_signal": cooperation.get("dispute_count")
            if cooperation
            else None,
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
    auth = (
        audience.get("authenticity_status")
        or audience.get("audience_authenticity")
        or ""
    )
    if isinstance(auth, str):
        auth_l = auth.lower()
    else:
        auth_l = str(auth).lower()
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
