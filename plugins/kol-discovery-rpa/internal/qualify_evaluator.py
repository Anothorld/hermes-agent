"""Qualification evaluator — mechanical hard-gate checker.

Inputs profile + reels data extracted by RPA tools and produces the
``qualification`` block that all fetch tools return. When
``hard_discard=True``, the Agent MUST discard the candidate and cannot
override with learned criteria (skill L100-103 priority rule).

Only mechanical thresholds are checked here. Subjective items
(product_context, match_score, showcase_score, learned_criteria_veto)
are listed in ``agent_judgment_required`` for the Agent to decide.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

import qualification_rules as rules  # noqa: E402
from followers_normalize import normalize_followers, is_borderline  # noqa: E402


def _gate(passed: bool, value: Any, threshold: Any, **extra: Any) -> dict:
    """Build a single gate result entry."""
    result = {"pass": passed, "value": value, "threshold": threshold}
    result.update(extra)
    return result


def evaluate_profile_gates(
    followers_raw: str | int | None,
    region_signals: str | list[str] | None,
    bio: str = "",
    bio_links: list[str] | None = None,
    is_business: bool | None = None,
) -> dict:
    """Evaluate profile-level hard gates (followers, region, account_type, furniture).

    Args:
        followers_raw: Raw follower text from IG (e.g. "125K", "73.8万").
        region_signals: Location signals from bio / profile (string or list).
        bio: Profile bio text.
        bio_links: Link-in-bio URLs.
        is_business: Whether IG marks this as a business account.

    Returns:
        Dict with ``gates`` (profile-level only) and ``discard_reasons``.
    """
    gates: dict[str, dict] = {}
    discard_reasons: list[str] = []

    # --- Followers gate ---
    followers = normalize_followers(followers_raw)
    followers_pass = followers >= rules.FOLLOWERS_MIN
    borderline = is_borderline(followers)
    gates["followers"] = _gate(
        followers_pass,
        followers,
        rules.FOLLOWERS_MIN,
        raw=str(followers_raw),
        borderline=borderline,
    )
    if not followers_pass:
        discard_reasons.append("followers_below_min")

    # --- Region gate ---
    region_value = _resolve_region(region_signals)
    region_pass = region_value in rules.REGIONS_ALLOWED
    if not region_pass and rules.REGION_UNKNOWN_DISCARD and region_value == "unknown":
        discard_reasons.append("region_unknown")
    gates["region"] = _gate(region_pass, region_value, "US|CA")

    # --- Account type heuristic (individual vs agency/brand) ---
    account_type = _heuristic_account_type(bio, is_business)
    gates["account_type"] = _gate(
        account_type["pass"],
        account_type["heuristic"],
        "individual",
        confidence=account_type["confidence"],
    )

    # --- Furniture self-commerce heuristic ---
    furniture = _heuristic_furniture_self_commerce(bio, bio_links)
    gates["furniture_self_commerce"] = _gate(
        not furniture["detected"],
        furniture["detected"],
        False,
        confidence=furniture["confidence"],
    )
    if furniture["detected"]:
        discard_reasons.append("furniture_self_commerce_heuristic")

    return {"gates": gates, "discard_reasons": discard_reasons}


def evaluate_reels_gates(
    reels: list[dict],
    profile_gates: dict | None = None,
) -> dict:
    """Evaluate reels-level hard gates (count_3mo, avg_views, er, static_only).

    Args:
        reels: List of reel dicts, each with ``views``, ``likes``, ``comments``,
               ``posted_at`` (ISO str) or ``posted_within_hours`` (int).
        profile_gates: Output of ``evaluate_profile_gates`` to merge gates.

    Returns:
        Dict with merged ``gates``, ``discard_reasons``, and ``hard_discard``.
    """
    from datetime import datetime, timedelta, timezone

    gates: dict = dict(profile_gates.get("gates", {})) if profile_gates else {}
    discard_reasons: list = list(profile_gates.get("discard_reasons", [])) if profile_gates else []

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=rules.REELS_WINDOW_DAYS)

    reels_3mo = 0
    views_for_avg: list[int] = []
    er_values: list[float] = []

    for reel in reels:
        posted_at = reel.get("posted_at")
        posted_within_hours = reel.get("posted_within_hours")

        # Determine if reel is within 3-month window
        in_window = False
        if posted_at:
            try:
                dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                in_window = dt >= window_start
            except (ValueError, TypeError):
                in_window = True  # Unknown date — count it
        elif posted_within_hours is not None:
            in_window = posted_within_hours <= rules.REELS_WINDOW_DAYS * 24
        else:
            in_window = True  # No date info — count it

        if in_window:
            reels_3mo += 1

        # Exclude reels posted within last 72h from avg views (skill L140)
        exclude_from_avg = False
        if posted_within_hours is not None:
            exclude_from_avg = posted_within_hours < rules.VIEWS_EXCLUDE_HOURS
        elif posted_at:
            try:
                dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                age_hours = (now - dt).total_seconds() / 3600
                exclude_from_avg = age_hours < rules.VIEWS_EXCLUDE_HOURS
            except (ValueError, TypeError):
                pass

        if not exclude_from_avg:
            views = int(reel.get("views", 0) or 0)
            likes = int(reel.get("likes", 0) or 0)
            comments = int(reel.get("comments", 0) or 0)
            if views > 0:
                views_for_avg.append(views)
                er = (likes + comments) / views
                er_values.append(er)

    # --- Reels count gate ---
    reels_pass = reels_3mo >= rules.REELS_MIN_3MO
    gates["reels_3mo"] = _gate(reels_pass, reels_3mo, rules.REELS_MIN_3MO)
    if not reels_pass:
        if reels_3mo == 0:
            discard_reasons.append("static_only_account")
        else:
            discard_reasons.append("reels_count_below_5")

    # --- Avg views gate (excluding 72h) ---
    avg_views = sum(views_for_avg) / len(views_for_avg) if views_for_avg else 0
    avg_views_pass = avg_views >= rules.AVG_VIEWS_MIN
    gates["avg_views_excl_72h"] = _gate(avg_views_pass, int(avg_views), rules.AVG_VIEWS_MIN)
    if not avg_views_pass and reels_3mo > 0:
        discard_reasons.append("avg_views_below_min")

    # --- Reel ER gate ---
    # The /reels/ grid only exposes views — likes and comments are always 0
    # from the grid extraction (ig_reels.py hardcodes them). Computing ER from
    # that would give 0% for every reel and hard-discard every candidate with
    # a false reel_er_below_min. When ALL reels have likes=0 AND comments=0,
    # the engagement data is simply unavailable (not genuinely 0%); defer the
    # ER gate to the agent (which gets real likes/comments from
    # rpa_fetch_reel_comments per reel page).
    all_engagement_missing = all(
        int(r.get("likes", 0) or 0) == 0 and int(r.get("comments", 0) or 0) == 0
        for r in reels
        if int(r.get("views", 0) or 0) > 0
    )
    avg_er = sum(er_values) / len(er_values) if er_values else 0
    if all_engagement_missing and er_values:
        # Grid couldn't provide likes/comments — defer, don't hard-discard.
        gates["reel_er"] = _gate(
            True,
            "deferred",
            rules.REEL_ER_MIN,
            reason="likes_comments_unavailable_from_grid",
            note="ER must be re-evaluated from rpa_fetch_reel_comments reel_likes/reel_comments_count",
        )
    else:
        er_pass = avg_er >= rules.REEL_ER_MIN
        gates["reel_er"] = _gate(er_pass, round(avg_er, 4), rules.REEL_ER_MIN)
        if not er_pass and er_values:
            discard_reasons.append("reel_er_below_min")

    hard_discard = len(discard_reasons) > 0

    return {
        "hard_discard": hard_discard,
        "discard_reasons": discard_reasons,
        "gates": gates,
        "agent_judgment_required": list(rules.AGENT_JUDGMENT_REQUIRED),
    }


def evaluate_exclusion_precheck(
    handle: str,
    exclusion_handles: list[str] | None = None,
    skip_handles: list[str] | None = None,
    cooldown_handles: list[str] | None = None,
    candidate_status_map: dict | None = None,
) -> dict:
    """Evaluate exclusion_set / skip / cooldown precheck (zero page load).

    Args:
        handle: The IG handle to check (with or without @).
        exclusion_handles: Handles already in CAL (from list-candidates).
        skip_handles: Handles in discovery skip set (competitor/success/etc).
        cooldown_handles: Handles in 14-day outreach cooldown.
        candidate_status_map: Optional handle→status map from --with-status.

    Returns:
        Dict with ``gates.exclusion_precheck`` and ``hard_discard`` if hit.
    """
    norm = handle.lower().lstrip("@")

    exclusion_set = {h.lower().lstrip("@") for h in (exclusion_handles or [])}
    skip_set = {h.lower().lstrip("@") for h in (skip_handles or [])}
    cooldown_set = {h.lower().lstrip("@") for h in (cooldown_handles or [])}

    discard_reasons: list[str] = []
    status = None

    if norm in exclusion_set:
        status = candidate_status_map.get(norm) if candidate_status_map else "in_pool"
        discard_reasons.append("already_in_pool")

    if norm in skip_set:
        discard_reasons.append("skip_list_active")

    if norm in cooldown_set:
        discard_reasons.append("outreach_cooldown_active")

    gate = _gate(
        len(discard_reasons) == 0,
        status,
        None,
        handle=norm,
    )

    return {
        "hard_discard": len(discard_reasons) > 0,
        "discard_reasons": discard_reasons,
        "gates": {"exclusion_precheck": gate},
        "agent_judgment_required": list(rules.AGENT_JUDGMENT_REQUIRED),
    }


# --- Heuristics ---

def _resolve_region(signals: str | list[str] | None) -> str:
    """Best-effort region resolution from profile location signals."""
    if signals is None:
        return "unknown"
    if isinstance(signals, str):
        signals = [signals]
    us_cities = (
        "los angeles", "new york", "nyc", "chicago", "miami", "seattle",
        "boston", "denver", "austin", "portland", "nashville", "atlanta",
        "san diego", "san francisco", "houston", "dallas", "phoenix",
        "philadelphia", "washington", "dc",
    )
    ca_cities = ("toronto", "vancouver", "montreal", "calgary", "ottawa", "edmonton")
    for sig in signals:
        sig_stripped = sig.strip()
        sig_upper = sig_stripped.upper()
        sig_lower = sig_stripped.lower()
        # Exact country code match
        if sig_upper in ("US", "USA"):
            return "US"
        if sig_upper in ("CA", "CAN"):
            # "CA" alone is ambiguous (California vs Canada) — check context
            # If it's a standalone "CA", treat as Canada (IG uses CA for Canada)
            if sig_upper == "CA" and len(sig_stripped) <= 3:
                return "CA"
        # US state abbreviations at end of string (e.g. "Los Angeles, CA")
        # But "CA" at end is ambiguous — check for US cities first
        if any(city in sig_lower for city in us_cities):
            return "US"
        if any(city in sig_lower for city in ca_cities):
            return "CA"
        if any(kw in sig_lower for kw in ("united states", "usa", "u.s.", "us,", " us ")):
            return "US"
        if any(kw in sig_lower for kw in ("canada", "canada,", "ca,", "bc,", "ontario")):
            return "CA"
    return "unknown"


def _heuristic_account_type(bio: str, is_business: bool | None) -> dict:
    """Heuristic: individual personal blogger vs agency/media/brand."""
    bio_lower = (bio or "").lower()
    agency_keywords = ("agency", "media company", "brand", "studio", "production company")
    is_agency = any(kw in bio_lower for kw in agency_keywords)

    if is_business is True and is_agency:
        return {"pass": False, "heuristic": "agency", "confidence": "high"}
    if is_business is True:
        return {"pass": True, "heuristic": "individual", "confidence": "medium"}
    if is_agency:
        return {"pass": False, "heuristic": "agency", "confidence": "medium"}
    return {"pass": True, "heuristic": "individual", "confidence": "low"}


def _heuristic_furniture_self_commerce(bio: str, bio_links: list[str] | None) -> dict:
    """Heuristic: detect furniture self-commerce (bio + link-in-bio keywords)."""
    bio_lower = (bio or "").lower()
    links_text = " ".join(bio_links or []).lower()

    for kw in rules.FURNITURE_SELF_COMMERCE_KEYWORDS:
        if kw in bio_lower or kw in links_text:
            return {"detected": True, "confidence": "medium", "keyword": kw}

    # Check for furniture storefront in links
    furniture_storefronts = ("amazon.com/shop", "ltk.app", "shopmy.us")
    has_storefront = any(sf in links_text for sf in furniture_storefronts)
    has_furniture_in_bio = "furniture" in bio_lower
    if has_storefront and has_furniture_in_bio:
        return {"detected": True, "confidence": "low", "keyword": "storefront+furniture"}

    return {"detected": False, "confidence": "low"}
