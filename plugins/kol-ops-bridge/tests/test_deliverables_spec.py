"""Tests for campaign deliverables spec parse + validation."""

from __future__ import annotations


def _ds(bridge_pkg):
    return bridge_pkg.deliverables_spec


def test_parse_cross_post_with_ad_code(bridge_pkg):
    ds = _ds(bridge_pkg)
    out = ds.parse_deliverables_text(
        "1 short video cross-post to IG, TikTok and YT Shorts; provide ad code; 30 days organic usage",
    )
    assert out["validation"]["valid"]
    assert set(out["deliverable_platforms"]) >= {"instagram", "tiktok", "youtube"}
    kinds = {row["kind"] for row in out["deliverables_spec"]}
    assert "platform_post" in kinds
    assert "ad_code" in kinds
    assert "usage_rights" in kinds


def test_build_platform_rows_fallback(bridge_pkg):
    ds = _ds(bridge_pkg)
    rows = ds.build_platform_rows(["instagram", "tiktok"], 1)
    assert len(rows) == 1
    assert rows[0]["kind"] == "platform_post"
    assert "Instagram" in rows[0]["platform_of_uploading"]


def test_resolve_from_legacy_platforms(bridge_pkg):
    ds = _ds(bridge_pkg)
    spec = ds.resolve_campaign_deliverables(
        {
            "deliverable_platforms": ["instagram"],
            "deliverable_count_per_platform": 2,
        },
    )
    assert spec
    assert spec[0]["quantity"] == "2 videos"


def test_resolve_stored_spec(bridge_pkg):
    ds = _ds(bridge_pkg)
    stored = [
        {
            "kind": "ad_code",
            "type": "Spark Code",
            "description": "TikTok spark code within 7 days",
            "quantity": "1",
        },
    ]
    spec = ds.resolve_campaign_deliverables(
        {"campaign_deliverables_json": stored, "deliverable_platforms": ["tiktok"]},
    )
    assert spec[0]["kind"] == "ad_code"


def test_validate_rejects_bad_kind(bridge_pkg):
    ds = _ds(bridge_pkg)
    verdict = ds.validate_spec([{"kind": "unknown", "type": "x"}])
    assert not verdict["valid"]


def test_build_contract_deliverables(bridge_pkg):
    ds = _ds(bridge_pkg)
    rows = ds.build_contract_deliverables(
        {
            "campaign_deliverables_json": [
                {
                    "kind": "ad_code",
                    "type": "Spark Code",
                    "description": "TikTok spark within 7 days",
                    "quantity": "1",
                },
            ],
            "deliverable_platforms": ["tiktok"],
            "deliverable_count_per_platform": 1,
        },
    )
    assert len(rows) == 1
    assert rows[0]["type"] == "Spark Code"
