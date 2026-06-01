from __future__ import annotations

import sqlite3

import pytest

from app.campaign_config_sync import build_campaign_config_upsert_body
from app.variant_candidates import (
    assert_email_has_no_internal_ids,
    email_option_line,
    normalize_variant,
    parse_variants_from_extra_notes,
    resolve_campaign_variants,
    variant_candidates_from_campaign_config,
)


def test_normalize_variant_strips_merchant_sku_from_email_helpers():
    v = normalize_variant(
        {
            "id": "37384",
            "label": "Green / Suede / 2 Armless",
            "url": "https://example.com?variant=37384",
            "attributes": {"color": "Green", "material": "Suede Fabric"},
            "merchant_sku": "SF8181G265",
            "price": 1999.0,
        }
    )
    assert v["id"] == "37384"
    assert v["merchant_sku"] == "SF8181G265"
    assert v["price"] == 1999.0


def test_email_option_line_uses_product_name_spec_and_url():
    v = normalize_variant(
        {
            "id": "1",
            "attributes": {"color": "Beige", "size": "Loveseat"},
            "url": "https://shop.example/p?variant=1",
        }
    )
    body = email_option_line(product_display_name="Cloud Sofa", variant=v)
    assert "Cloud Sofa" in body
    assert "Beige" in body
    assert "View option:" in body
    assert "variant 1" not in body.lower()
    assert "SF8181" not in body


def test_assert_email_has_no_internal_ids_rejects_leaks():
    with pytest.raises(ValueError):
        assert_email_has_no_internal_ids("Happy to send SKU: SF8181G265")


def test_resolve_campaign_variants_filters_and_synthesizes():
    catalog = [
        {"id": "a", "label": "A", "attributes": {"color": "A"}},
        {"id": "b", "label": "B", "attributes": {"color": "B"}},
    ]
    picked = resolve_campaign_variants(
        product_variants=catalog,
        selected_ids=["b"],
        product_sku="SKU-1",
        product_name="Name",
        product_url=None,
    )
    assert len(picked) == 1
    assert picked[0]["id"] == "b"

    synthetic = resolve_campaign_variants(
        product_variants=[],
        selected_ids=None,
        product_sku="SKU-1",
        product_name="Name",
        product_url="https://x/y",
    )
    assert len(synthetic) == 1
    assert synthetic[0]["url"] == "https://x/y"


def test_variant_candidates_from_campaign_config_prefers_column():
    cfg = {
        "variant_candidates": [{"id": "9", "label": "Green", "attributes": {"color": "Green"}}],
        "extra_notes": '# product_variants\n[{"id":"legacy"}]',
    }
    out = variant_candidates_from_campaign_config(cfg)
    assert out[0]["id"] == "9"


def test_parse_variants_from_extra_notes():
    notes = '# selling_points\nx\n\n# product_variants\n[{"id":"55","label":"Gray","attributes":{"color":"Gray"}}]'
    out = parse_variants_from_extra_notes(notes)
    assert out[0]["id"] == "55"


def test_build_campaign_config_upsert_body_binds_whitelist_and_candidates():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    product = conn.execute(
        "SELECT 'SKU' AS sku, 'Sofa' AS name, 'http://p' AS url, "
        "'[]' AS tags_json, NULL AS notes, NULL AS pitch_md, "
        "'points' AS selling_points, '[]' AS variants_json"
    ).fetchone()
    assert product is not None

    class Body:
        product_display_name = "Cloud Sofa"
        budget_per_kol = 500.0
        test_mode_to = None
        env = "TEST"
        deliverable_platforms = ["instagram"]
        deliverable_count_per_platform = 1
        audit_standards_md = None

    selected = [
        {
            "id": "37384",
            "label": "Green / Suede",
            "url": "https://p?variant=37384",
            "attributes": {"color": "Green"},
            "price": 1999.0,
        }
    ]
    upsert = build_campaign_config_upsert_body(
        product=product,
        body=Body(),  # type: ignore[arg-type]
        selected_variants=selected,
        sku_ref="http://p",
    )
    assert upsert["sku_whitelist"] == ["37384"]
    assert upsert["variant_candidates"][0]["id"] == "37384"
    assert upsert["variant_candidates"][0]["price"] == 1999.0
    assert "Green" in (upsert.get("color_variant_policy") or "")
