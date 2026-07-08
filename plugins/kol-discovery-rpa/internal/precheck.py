"""Exclusion precheck — zero page-load handle screening.

Called before any ``rpa_fetch_ig_profile`` to avoid wasting a navigate
on handles already in CAL / skip list / outreach cooldown. The Agent
passes the bootstrap exclusion sets; this module delegates to
``qualify_evaluator.evaluate_exclusion_precheck``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from qualify_evaluator import evaluate_exclusion_precheck  # noqa: E402


def precheck_handle(
    handle: str,
    exclusion_handles: list[str] | None = None,
    skip_handles: list[str] | None = None,
    cooldown_handles: list[str] | None = None,
    candidate_status_map: dict | None = None,
) -> dict:
    """Precheck a handle against exclusion sets without any page load.

    Args:
        handle: IG handle to check (with or without @).
        exclusion_handles: Handles already in CAL (from list-candidates).
        skip_handles: Handles in discovery skip set.
        cooldown_handles: Handles in 14-day outreach cooldown.
        candidate_status_map: Optional handle→status from --with-status.

    Returns:
        Qualification dict with ``hard_discard`` and ``gates.exclusion_precheck``.
    """
    return evaluate_exclusion_precheck(
        handle,
        exclusion_handles=exclusion_handles,
        skip_handles=skip_handles,
        cooldown_handles=cooldown_handles,
        candidate_status_map=candidate_status_map,
    )
