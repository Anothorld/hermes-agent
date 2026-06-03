"""Tests for Gate A diligence fact hydration."""

from __future__ import annotations

import sys
from pathlib import Path

_NOX = Path(__file__).resolve().parents[4] / "plugins" / "nox-kol-bridge"
if str(_NOX) not in sys.path:
    sys.path.insert(0, str(_NOX))

from internal.diligence_facts import identity_facts_from_diligence  # noqa: E402

from app.nox_diligence_sync import (  # noqa: E402
    diligence_eligible,
    resolve_diligence_params,
)


def test_resolve_diligence_params_creator_id():
    ident = {"platform": "instagram", "primary_handle": "kol"}
    facts = {"facts": {"identity.nox_creator_id": "RH2Y"}}
    params = resolve_diligence_params(ident, facts)
    assert params["nox_creator_id"] == "RH2Y"
    assert diligence_eligible(params)


def test_identity_facts_includes_engagement_from_summary():
    facts = identity_facts_from_diligence(
        {
            "cache_month": "2026-06",
            "cache_key": "diligence_pack|id|profile|en",
            "normalized_summary": {
                "nox_creator_id": "id",
                "nox_diligence_verdict": "high_priority",
                "engagement_rate": 0.0302,
                "avg_views": 188519,
                "audience_top_regions": "US (74.8%)",
            },
        },
        at_iso="2026-06-03T08:24:00Z",
    )
    assert facts["identity.nox_engagement_rate"] == 0.0302
    assert facts["identity.nox_avg_views"] == 188519
    assert facts["identity.nox_top_region"] == "US (74.8%)"
