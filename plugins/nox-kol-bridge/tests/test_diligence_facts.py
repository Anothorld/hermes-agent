"""Tests for CAL fact mapping from diligence envelopes."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.diligence_facts import identity_facts_from_diligence  # noqa: E402
from internal.summarize import summarize_diligence_pack  # noqa: E402


def test_identity_facts_from_diligence_maps_core_fields():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "nox-abc",
                "creator_name": "Creator X",
                "followers": 120000,
                "engagement_rate": 0.031,
                "avg_views": 50000,
                "country": "US",
                "channel_url": "https://instagram.com/x",
            },
        },
        "audience": {
            "success": True,
            "data": {
                "regions": [{"country": "US", "percent": 0.75}],
                "female_ratio": 0.82,
                "audience_authenticity": {"value": 0.84, "status": 4},
            },
        },
        "content": {
            "success": True,
            "data": {"tags": ["home", "collab"]},
        },
    }
    summary = summarize_diligence_pack(bundle)
    envelope = {
        "cache_hit": True,
        "cache_month": "2026-06",
        "cache_key": "diligence_pack|nox-abc|audience,content,profile|en",
        "api_calls": 0,
        "normalized_summary": summary,
        "facts_hint": {
            "identity.nox_cache_month": "2026-06",
            "identity.nox_cache_key": "diligence_pack|nox-abc|audience,content,profile|en",
        },
    }
    facts = identity_facts_from_diligence(envelope, at_iso="2026-06-03T08:00:00Z")
    assert facts["identity.nox_creator_id"] == "nox-abc"
    assert facts["identity.nox_audience_authenticity"] == 0.84
    assert facts["identity.followers"] == 120000
    assert facts["identity.nox_top_region"] == "US (75.0%)"
    assert "female" in facts["identity.nox_gender_skew"]
    assert facts["identity.nox_diligence_verdict"] in (
        "high_priority",
        "needs_manual_review",
        "viable_with_risks",
        "not_priority",
    )
    assert facts["identity.nox_cache_hit"] is True
    assert "identity.nox_diligence_at" in facts


def test_identity_facts_persist_nox_score_breakdown():
    bundle = {
        "profile": {
            "success": True,
            "data": {
                "creator_id": "nox-abc",
                "nox_score": {
                    "overall": 55,
                    "growth": 60,
                    "creativity": 50,
                    "audience": 58,
                    "engagement": 52,
                    "credibility": 54,
                },
            },
        },
        "audience": {"success": True, "data": {}},
        "content": {"success": True, "data": {}},
    }
    summary = summarize_diligence_pack(bundle)
    facts = identity_facts_from_diligence(
        {"normalized_summary": summary},
        at_iso="2026-06-03T08:00:00Z",
    )
    assert facts["identity.nox_score"] == 55
    assert '"overall":55' in facts["identity.nox_score_breakdown"]


def test_summarize_infers_followers_from_instagram_cache_bundle():
    import json
    import sqlite3
    from pathlib import Path

    cache_db = (
        Path.home()
        / ".hermes/profiles/kol-orchestrator/kol-ops-bridge/nox_cache/nox_cache.db"
    )
    if not cache_db.is_file():
        return
    conn = sqlite3.connect(str(cache_db))
    row = conn.execute(
        "SELECT response_json FROM entries WHERE cache_key LIKE 'diligence_pack|RH2Yj62w%'",
    ).fetchone()
    conn.close()
    if not row:
        return
    bundle = json.loads(row[0])
    summary = summarize_diligence_pack(bundle)
    assert summary.get("followers") is not None
    assert summary.get("followers_source") == "inferred_views_ratio"
    assert int(summary["followers"]) > 100_000
