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
    if not settings.discovery_learned_criteria or not sku:
        return ""
    try:
        data: dict[str, Any] = await bridge.get_discovery_criteria(
            sku=sku,
            env=env,
            max_chars=settings.discovery_learned_criteria_max_chars,
        )
    except Exception as exc:  # noqa: BLE001 — launch must not block on learning
        log.warning("learned discovery criteria unavailable (sku=%s): %s", sku, exc)
        return ""
    spu_md = str(data.get("spu_md") or "").strip()
    category_md = str(data.get("category_md") or "").strip()
    if not spu_md and not category_md:
        return ""
    lines = ["", BRIEF_SECTION_HEADER, _SECTION_PREAMBLE]
    if spu_md:
        lines.extend(["", f"## product-level criteria (sku={sku})", spu_md])
    if category_md:
        category = str(data.get("category") or "")
        lines.extend(["", f"## category-level criteria (category={category})", category_md])
    return "\n".join(lines)
