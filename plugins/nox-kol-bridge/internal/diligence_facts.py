"""Map Nox diligence CLI envelopes to CAL ``identity.*`` fact keys."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Mapping, Optional


def parse_compact_count(value: Any) -> Optional[int]:
    """Parse ``416K``, ``12万``, ``1.2M`` style counts to integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    if not isinstance(value, str):
        return None
    s = value.strip().upper().replace(",", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    elif s.endswith("万"):
        mult = 10_000
        s = s[:-1]
    elif s.endswith("W") and len(s) > 1:
        mult = 10_000
        s = s[:-1]
    try:
        n = float(s) * mult
        return int(round(n)) if n > 0 else None
    except ValueError:
        return None


def _unwrap_nox_metric(value: Any) -> Any:
    if isinstance(value, dict):
        if "value" in value:
            return value.get("value")
        if "score" in value:
            return value.get("score")
        if "overall" in value:
            return value.get("overall")
    return value


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    value = _unwrap_nox_metric(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def identity_facts_from_diligence(
    envelope: Mapping[str, Any],
    *,
    at_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Build ``identity.*`` facts from a ``diligence-pack`` tool envelope."""
    summary = envelope.get("normalized_summary")
    if not isinstance(summary, dict):
        summary = {}
    hints = envelope.get("facts_hint")
    if not isinstance(hints, dict):
        hints = {}

    facts: dict[str, Any] = {}
    now = at_iso or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def put(key: str, value: Any) -> None:
        v = _scalar(value)
        if v is None or v == "":
            return
        facts[f"identity.{key}"] = v

    put("nox_diligence_at", now)
    put("nox_diligence_verdict", summary.get("nox_diligence_verdict"))
    put("nox_creator_id", summary.get("nox_creator_id"))
    put("nox_creator_name", summary.get("creator_name"))
    put("nox_cache_month", envelope.get("cache_month") or hints.get("identity.nox_cache_month"))
    put("nox_cache_key", envelope.get("cache_key") or hints.get("identity.nox_cache_key"))
    put("nox_cache_hit", envelope.get("cache_hit"))
    put("nox_api_calls_last", envelope.get("api_calls"))

    put("nox_followers", summary.get("followers"))
    put("followers", summary.get("followers"))
    src = summary.get("followers_source")
    if src:
        put("nox_followers_source", src)
    put("nox_engagement_rate", summary.get("engagement_rate"))
    put("nox_avg_views", summary.get("avg_views"))
    put("nox_country", summary.get("country"))
    put("region", summary.get("country"))
    put("nox_score", summary.get("nox_score"))
    breakdown = summary.get("nox_score_breakdown")
    if isinstance(breakdown, dict):
        put("nox_score_breakdown", json.dumps(breakdown, ensure_ascii=False, separators=(",", ":")))
    put("nox_platform", summary.get("platform"))
    put("nox_top_region", summary.get("audience_top_regions"))
    put("nox_audience_authenticity", summary.get("audience_authenticity"))
    put("nox_audience_quality_score", summary.get("audience_quality_score"))
    put("nox_gender_skew", summary.get("gender_skew"))
    put("nox_audience_interests_top", summary.get("audience_interests_top"))
    put("nox_content_tags_top", summary.get("content_tags_top"))
    put("nox_benchmark_rank", summary.get("benchmark_rank"))
    put("nox_channel_url", summary.get("channel_url"))
    put("nox_dispute_count", summary.get("dispute_count"))
    put("nox_diligence_dimensions", summary.get("diligence_dimensions"))
    put("nox_diligence_lang", summary.get("diligence_lang"))
    ck_raw = envelope.get("cache_key") or hints.get("identity.nox_cache_key")
    if isinstance(ck_raw, str) and ck_raw.startswith("diligence_pack|"):
        parts = ck_raw.split("|")
        if len(parts) >= 4:
            if "identity.nox_diligence_dimensions" not in facts:
                put("nox_diligence_dimensions", parts[2])
            if "identity.nox_diligence_lang" not in facts:
                put("nox_diligence_lang", parts[3])

    hl = summary.get("highlights")
    if isinstance(hl, dict):
        put("nox_audience_authenticity", hl.get("audience_authenticity"))
        put("nox_benchmark_rank", hl.get("benchmark_rank"))
        put("nox_channel_url", hl.get("channel_url"))
        put("nox_dispute_count", hl.get("dispute_signal"))

    return facts


def identity_facts_from_contacts(
    envelope: Mapping[str, Any],
    *,
    email: Optional[str] = None,
    at_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Gate B contacts envelope → identity facts (email written separately)."""
    summary = envelope.get("normalized_summary")
    if not isinstance(summary, dict):
        summary = {}
    now = at_iso or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    facts: dict[str, Any] = {
        "identity.nox_contacts_at": now,
        "identity.nox_contacts_cache_hit": envelope.get("cache_hit"),
    }
    month = envelope.get("cache_month") or summary.get("identity.nox_contacts_cached_month")
    if month:
        facts["identity.nox_contacts_cached_month"] = month
        facts["identity.nox_cache_month"] = month
    ck = envelope.get("cache_key")
    if ck:
        facts["identity.nox_contacts_cache_key"] = ck
    cid = summary.get("nox_creator_id")
    if cid:
        facts["identity.nox_creator_id"] = cid
    quality = summary.get("email_quality")
    if quality is not None:
        facts["identity.nox_email_quality"] = quality
    if email:
        facts["identity.email"] = email.strip().lower()
        facts["identity.email_source"] = "noxinfluencer_api"
        facts["identity.email_discovered_at"] = now
    return facts


def merge_existing_follower_facts(
    facts: dict[str, Any],
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    """Fill followers from CAL when Nox diligence bundle omitted them."""
    if facts.get("identity.followers") or facts.get("identity.nox_followers"):
        return facts
    for key in ("identity.followers", "identity.follower_count", "identity.fans_count"):
        parsed = parse_compact_count(existing.get(key))
        if parsed:
            facts["identity.followers"] = parsed
            facts["identity.nox_followers"] = parsed
            facts["identity.nox_followers_source"] = "cal_existing"
            break
    return facts


__all__ = [
    "identity_facts_from_contacts",
    "identity_facts_from_diligence",
    "merge_existing_follower_facts",
    "parse_compact_count",
]
