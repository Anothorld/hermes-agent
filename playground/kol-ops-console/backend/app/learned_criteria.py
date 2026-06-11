"""Inject learned discovery criteria into discovery launch briefs.

Reads the approved ``discovery_criteria:spu:*`` / ``discovery_criteria:category:*``
policies through the bridge and appends them to the launch / rediscover brief
as a ``# learned_discovery_criteria`` section. Strictly best-effort: when the
bridge or the learning channel is unavailable, the section is omitted and the
launch proceeds unchanged (WARNING logged) — discovery must never be blocked
by the learning loop.

Toggle: ``KOC_DISCOVERY_LEARNED_CRITERIA`` (default true).
Budget: ``KOC_DISCOVERY_LEARNED_CRITERIA_MAX_CHARS`` (default 4000), SPU
criteria take priority and the category criteria fill the remaining budget.
"""

from __future__ import annotations

import logging
from typing import Any

from .bridge_client import BridgeClient
from .config import get_settings

log = logging.getLogger(__name__)

BRIEF_SECTION_HEADER = "# learned_discovery_criteria"

_SECTION_PREAMBLE = (
    "Operator-approved learned criteria distilled from past shortlist "
    "decisions. Apply them when scoring and selecting candidates: they "
    "ADJUST Match/Showcase emphasis and add soft veto signals, but NEVER "
    "relax the skill's HARD thresholds."
)


async def learned_criteria_brief_section(
    bridge: BridgeClient, *, sku: str | None, env: str,
) -> str:
    """Return the brief section text, or ``""`` when nothing applies."""
    settings = get_settings()
    # #region agent log
    def _dbg(message: str, data: dict[str, Any], hypothesis_id: str) -> None:
        try:
            import json as _json
            import time as _time
            from pathlib import Path as _Path

            with _Path("/Users/arnold/agent_prj/.cursor/debug-8ea4a0.log").open(
                "a", encoding="utf-8",
            ) as _fh:
                _fh.write(
                    _json.dumps(
                        {
                            "sessionId": "8ea4a0",
                            "hypothesisId": hypothesis_id,
                            "location": "learned_criteria.py:learned_criteria_brief_section",
                            "message": message,
                            "data": data,
                            "timestamp": int(_time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                )
        except Exception:
            pass

    _dbg(
        "learned_criteria_entry",
        {
            "sku": sku,
            "env": env,
            "toggle": settings.discovery_learned_criteria,
            "max_chars": settings.discovery_learned_criteria_max_chars,
        },
        "H1",
    )
    # #endregion
    if not settings.discovery_learned_criteria or not sku:
        # #region agent log
        _dbg(
            "learned_criteria_skip_toggle_or_sku",
            {"sku": sku, "toggle": settings.discovery_learned_criteria},
            "H1",
        )
        # #endregion
        return ""
    try:
        data: dict[str, Any] = await bridge.get_discovery_criteria(
            sku=sku,
            env=env,
            max_chars=settings.discovery_learned_criteria_max_chars,
        )
    except Exception as exc:  # noqa: BLE001 — launch must not block on learning
        log.warning("learned discovery criteria unavailable (sku=%s): %s", sku, exc)
        # #region agent log
        _dbg(
            "learned_criteria_bridge_error",
            {"sku": sku, "env": env, "error": str(exc)[:300]},
            "H1",
        )
        # #endregion
        return ""
    spu_md = str(data.get("spu_md") or "").strip()
    category_md = str(data.get("category_md") or "").strip()
    # #region agent log
    _dbg(
        "learned_criteria_bridge_response",
        {
            "sku": sku,
            "env": env,
            "category": data.get("category"),
            "spu_md_len": len(spu_md),
            "category_md_len": len(category_md),
        },
        "H1",
    )
    # #endregion
    if not spu_md and not category_md:
        # #region agent log
        _dbg("learned_criteria_empty_policy", {"sku": sku, "env": env}, "H1")
        # #endregion
        return ""
    lines = ["", BRIEF_SECTION_HEADER, _SECTION_PREAMBLE]
    if spu_md:
        lines.extend(["", f"## product-level criteria (sku={sku})", spu_md])
    if category_md:
        category = str(data.get("category") or "")
        lines.extend(["", f"## category-level criteria (category={category})", category_md])
    section = "\n".join(lines)
    # #region agent log
    _dbg(
        "learned_criteria_section_built",
        {
            "sku": sku,
            "env": env,
            "section_len": len(section),
            "has_header": BRIEF_SECTION_HEADER in section,
        },
        "H2",
    )
    # #endregion
    return section
