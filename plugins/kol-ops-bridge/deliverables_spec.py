"""Deterministic campaign deliverables spec (intake parse + runtime read).

Operators describe deliverables in natural language **only at campaign launch**.
This module parses/heuristic-extracts a structured ``campaign_deliverables_json``
array (contract table rows) and derives ``deliverable_platforms`` /
``deliverable_count_per_platform``. Runtime reply/contract paths **read** the
stored spec — they do not re-parse NL.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from . import campaign_validation as cv

ALLOWED_KINDS = frozenset({"platform_post", "ad_code", "usage_rights", "custom"})

_PLATFORM_ALIASES: dict[str, str] = {
    "ig": "instagram",
    "instagram": "instagram",
    "insta": "instagram",
    "tt": "tiktok",
    "tiktok": "tiktok",
    "yt": "youtube",
    "youtube": "youtube",
    "shorts": "youtube",
    "twitter": "twitter",
    "x": "twitter",
    "blog": "blog",
}

_PLATFORM_LABELS = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "youtube": "YouTube Shorts",
    "twitter": "Twitter/X",
    "blog": "Blog",
}


def _platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(platform, platform.title())


def build_platform_rows(
    platforms: list[str],
    count: int,
    *,
    requirements: str = "",
) -> list[dict[str, Any]]:
    """Auto-generate default platform_post rows from legacy fields."""
    rows: list[dict[str, Any]] = []
    label = " + ".join(_platform_label(p) for p in platforms)
    qty = f"{count} video{'s' if count != 1 else ''}" if count else "1 video"
    rows.append(
        {
            "kind": "platform_post",
            "type": "Short-form video cross-post",
            "description": f"Original content on {label}",
            "quantity": qty,
            "requirements": requirements or "Vertical 20-60s, on-brand",
            "platform_of_uploading": label,
        },
    )
    return rows


def validate_row(row: Mapping[str, Any], *, index: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, Mapping):
        return [f"row[{index}] must be an object"]
    kind = row.get("kind") or "platform_post"
    if kind not in ALLOWED_KINDS:
        errors.append(f"row[{index}].kind must be one of {sorted(ALLOWED_KINDS)}")
    if not str(row.get("type") or "").strip():
        errors.append(f"row[{index}].type is required")
    has_desc = bool(str(row.get("description") or "").strip())
    has_platform = bool(str(row.get("platform_of_uploading") or "").strip())
    if kind == "platform_post" and not has_platform and not has_desc:
        errors.append(
            f"row[{index}] platform_post needs platform_of_uploading or description",
        )
    if kind in {"ad_code", "usage_rights", "custom"} and not has_desc:
        errors.append(f"row[{index}] {kind} needs description")
    return errors


def validate_spec(spec: Any) -> dict[str, Any]:
    """Validate a deliverables spec list."""
    if spec is None:
        return {"valid": True, "errors": [], "normalized": []}
    if not isinstance(spec, list):
        return {"valid": False, "errors": ["campaign_deliverables_json must be a list"], "normalized": []}
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    for i, row in enumerate(spec):
        if not isinstance(row, Mapping):
            errors.append(f"row[{i}] must be an object")
            continue
        row_errors = validate_row(row, index=i)
        errors.extend(row_errors)
        if not row_errors:
            out = dict(row)
            out.setdefault("kind", "platform_post")
            normalized.append(out)
    return {"valid": not errors, "errors": errors, "normalized": normalized}


def resolve_campaign_deliverables(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Effective deliverables rows for runtime (contract, implicit accept)."""
    raw = cfg.get("campaign_deliverables_json")
    if isinstance(raw, list) and raw:
        verdict = validate_spec(raw)
        if verdict["valid"] and verdict["normalized"]:
            return verdict["normalized"]
    platforms = cfg.get("deliverable_platforms") or []
    if not isinstance(platforms, list) or not platforms:
        return []
    count = cfg.get("deliverable_count_per_platform") or 1
    try:
        count = cv.normalize_deliverable_count_per_platform(count) or 1
    except ValueError:
        count = 1
    return build_platform_rows([str(p) for p in platforms], int(count))


def spec_search_phrases(spec: list[Mapping[str, Any]]) -> list[str]:
    """Keywords/phrases for thread-evidence matching (implicit accept)."""
    phrases: list[str] = []
    for row in spec:
        for key in (
            "type",
            "description",
            "requirements",
            "platform_of_uploading",
            "quantity",
        ):
            val = row.get(key)
            if isinstance(val, str) and val.strip():
                phrases.append(val.strip().lower())
        kind = row.get("kind")
        if kind == "ad_code":
            phrases.extend(["ad code", "spark code", "authorization code"])
        if kind == "usage_rights":
            phrases.extend(["usage rights", "organic usage", "usage period"])
    return phrases


def _detect_platforms(text: str) -> tuple[list[str], list[int]]:
    platforms: list[str] = []
    counts: list[int] = []
    lower = text.lower()
    for alias, canonical in _PLATFORM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lower, re.IGNORECASE):
            if canonical not in platforms:
                platforms.append(canonical)
    cross = re.search(
        r"(cross[\s-]?post|crosspost|同步发布|交叉发布)",
        lower,
        re.IGNORECASE,
    )
    if cross and len(platforms) >= 2:
        counts = [1] * len(platforms)
    for alias, canonical in _PLATFORM_ALIASES.items():
        match = re.search(
            rf"\b{re.escape(alias)}\b[\s xX×*：:]*(\d+)",
            text,
            re.IGNORECASE,
        )
        if match and canonical in platforms:
            idx = platforms.index(canonical)
            if idx < len(counts):
                counts[idx] = int(match.group(1))
            else:
                while len(counts) < len(platforms):
                    counts.append(1)
                counts[idx] = int(match.group(1))
    if platforms and not counts:
        counts = [1] * len(platforms)
    if (m := re.search(r"(\d+)\s*(?:条|个|篇|piece|video|reel|post)", lower)):
        n = int(m.group(1))
        counts = [n] * len(platforms) if platforms else [n]
    return platforms, counts


def parse_deliverables_text(text: str) -> dict[str, Any]:
    """Heuristic NL → structured deliverables (no DB write, no LLM)."""
    raw = (text or "").strip()
    unparsed: list[str] = []
    if not raw:
        return {
            "deliverables_spec": [],
            "deliverable_platforms": [],
            "deliverable_count_per_platform": None,
            "unparsed": ["empty text"],
            "validation": {"valid": False, "errors": ["text is required"], "normalized": []},
        }

    platforms, counts = _detect_platforms(raw)
    count: Optional[int] = None
    if counts:
        if len(set(counts)) == 1:
            count = counts[0]
        else:
            count = counts[0]
            unparsed.append(
                "per-platform counts differ "
                f"({dict(zip(platforms, counts))}); using first={count}",
            )

    lower = raw.lower()
    spec: list[dict[str, Any]] = []
    requirements_parts: list[str] = []

    if re.search(r"\b(english|英文)\b.*\b(caption|subtitle|字幕)", lower):
        requirements_parts.append("English captions")
    if re.search(r"20[\s-]*60\s*s|20[\s-]*60\s*秒", lower):
        requirements_parts.append("20-60s vertical")
    if re.search(r"\bvertical\b|竖屏|竖版", lower):
        requirements_parts.append("Vertical format")

    if platforms:
        spec.extend(
            build_platform_rows(
                platforms,
                count or 1,
                requirements=", ".join(requirements_parts) or "",
            ),
        )
    elif re.search(r"video|reel|短视频|视频", lower):
        unparsed.append("video mentioned but no platform detected — pick platforms manually")

    usage_match = re.search(
        r"(\d+)\s*(?:day|days|天)\s*(?:organic\s*)?(?:usage|使用权|使用)",
        lower,
    )
    if re.search(r"usage\s*right|organic\s*usage|使用权|organic usage", lower):
        period = usage_match.group(0) if usage_match else "as agreed"
        spec.append(
            {
                "kind": "usage_rights",
                "type": "Organic usage rights",
                "description": f"Brand organic repost rights — {period}",
                "quantity": "1",
                "requirements": period,
            },
        )

    if re.search(
        r"ad\s*code|spark\s*code|广告码|授权码|ad authorization",
        lower,
        re.IGNORECASE,
    ):
        per_plat = "1 per platform" if len(platforms) > 1 else "1"
        spec.append(
            {
                "kind": "ad_code",
                "type": "Ad Code / Spark Code",
                "description": "Provide platform ad authorization / spark code after publish",
                "quantity": per_plat,
                "requirements": "Within 7 days of go-live",
            },
        )

    if not spec:
        unparsed.append("could not extract deliverables — refine wording or use checkboxes")

    platform_errors: list[str] = []
    if platforms:
        bad = [p for p in platforms if p not in cv._PLATFORMS]
        if bad:
            platform_errors.append(f"unknown platforms: {bad}")

    spec_validation = validate_spec(spec)
    valid = bool(spec) and spec_validation["valid"] and not platform_errors

    return {
        "deliverables_spec": spec_validation.get("normalized") or spec,
        "deliverable_platforms": platforms,
        "deliverable_count_per_platform": count,
        "unparsed": unparsed,
        "validation": {
            "valid": valid,
            "errors": spec_validation["errors"] + platform_errors,
            "normalized": spec_validation.get("normalized") or spec,
        },
    }


def build_contract_deliverables(cfg: Mapping[str, Any]) -> list[dict[str, str]]:
    """Normalize ``campaign_config`` rows for ``render_contract.py``."""
    rows: list[dict[str, str]] = []
    for row in resolve_campaign_deliverables(cfg):
        rows.append(
            {
                "type": str(row.get("type") or ""),
                "description": str(row.get("description") or ""),
                "quantity": str(row.get("quantity") or ""),
                "requirements": str(
                    row.get("requirements") or row.get("length") or "",
                ),
                "time_of_uploading": str(row.get("time_of_uploading") or ""),
                "platform_of_uploading": str(
                    row.get("platform_of_uploading") or "",
                ),
            },
        )
    return rows


__all__ = [
    "ALLOWED_KINDS",
    "build_platform_rows",
    "build_contract_deliverables",
    "validate_row",
    "validate_spec",
    "resolve_campaign_deliverables",
    "spec_search_phrases",
    "parse_deliverables_text",
]
