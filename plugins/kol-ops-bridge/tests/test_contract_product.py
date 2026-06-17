"""Tests for deterministic contract product link/spec resolution."""

from __future__ import annotations

from kol_ops_bridge_pkg import contract_product as cp


def test_match_variant_by_label_not_only_id():
    variants = [
        {
            "id": "40300",
            "label": "French Retro Cream",
            "url": "https://www.povison.com/x?variant=40300",
            "attributes": {},
        },
        {
            "id": "8008-BROWN",
            "label": "Light Chenille Brown",
            "url": "https://www.povison.com/chenille-brown.html?variant=8008",
            "attributes": {"color": "Light Chenille Brown", "sku": "SEB8008K295"},
        },
    ]
    matched = cp.match_locked_variant(variants, "Light Chenille Brown")
    assert matched is not None
    assert matched["url"] == "https://www.povison.com/chenille-brown.html?variant=8008"


def test_resolve_product_link_falls_back_to_campaign_url():
    cfg = {
        "product_url": "https://www.povison.com/base?variant=40300",
        "product_display_name": "Aurora-Power Sofa Bed",
        "extra_notes": (
            '# product_variants\n[{"id": "40300", "label": "variant 40300", '
            '"url": "https://www.povison.com/base?variant=40300", "attributes": {}}]'
        ),
    }
    link, matched = cp.resolve_product_link(
        variant_locked="Light Chenille Brown",
        campaign_cfg=cfg,
        fetch_live=False,
    )
    assert link == "https://www.povison.com/base?variant=40300"
    assert matched is None


def test_load_variant_catalog_merges_live_api_rows(monkeypatch):
    cfg = {
        "product_url": (
            "https://www.povison.com/116-1-electric-sofa-with-a-retractable-feature"
            "-in-french-retro-cream-style.html?variant=40300"
        ),
        "extra_notes": (
            '# product_variants\n[{"id": "40300", "label": "variant 40300", '
            '"url": "https://www.povison.com/base?variant=40300", "attributes": {}}]'
        ),
    }

    def _fake_parse(url: str):
        assert "povison.com" in url
        return [
            {
                "id": "41550",
                "label": "3 Seater / Light Brown Chenille Fabric",
                "url": (
                    "https://www.povison.com/116-1-electric-sofa-with-a-retractable-feature"
                    "-in-french-retro-cream-style.html?variant=41550"
                ),
                "merchant_sku": "SEB8008K295",
                "attributes": {
                    "size": "3 Seater",
                    "color": "Light Brown Chenille Fabric",
                },
            },
            {
                "id": "40300",
                "label": "3 Seater / French Retro Cream",
                "url": "https://www.povison.com/base?variant=40300",
                "merchant_sku": "SEB8008K403",
                "attributes": {"size": "3 Seater", "color": "French Retro Cream"},
            },
        ]

    monkeypatch.setattr(cp.pv, "parse_variants_from_url", _fake_parse)
    catalog = cp.load_variant_catalog(cfg, fetch_live=True)
    assert {v["id"] for v in catalog} == {"40300", "41550"}
    link, matched = cp.resolve_product_link(
        variant_locked="Light Chenille Brown",
        campaign_cfg=cfg,
        fetch_live=True,
    )
    assert matched is not None
    assert matched["id"] == "41550"
    assert matched["merchant_sku"] == "SEB8008K295"
    assert "variant=41550" in link
    specs = cp.build_product_specs(
        sku_locked="SEB8008",
        variant_locked="Light Chenille Brown",
        campaign_cfg={"label": "Aurora-Power Sofa Bed"},
        matched_variant=matched,
    )
    assert "SEB8008K295" in specs
    assert "Light Brown Chenille Fabric" in specs


def test_match_variant_prefers_3_seater_chenille_brown():
    variants = [
        {
            "id": "41549",
            "merchant_sku": "SEB8008K210",
            "url": "https://www.povison.com/x?variant=41549",
            "attributes": {
                "color": "Light Brown",
                "material": "Chenille Fabric",
                "size": "2 Seater",
            },
        },
        {
            "id": "41550",
            "merchant_sku": "SEB8008K295",
            "url": "https://www.povison.com/x?variant=41550",
            "attributes": {
                "color": "Light Brown",
                "material": "Chenille Fabric",
                "size": "3 Seater",
            },
        },
    ]
    matched = cp.match_locked_variant(variants, "Light Chenille Brown")
    assert matched is not None
    assert matched["id"] == "41550"
    assert matched["merchant_sku"] == "SEB8008K295"


def test_build_product_specs_sales_format():
    cfg = {"label": "Aurora-Power Sofa Bed"}
    matched = {
        "merchant_sku": "SEB8008K295",
        "attributes": {
            "color": "Light Brown",
            "material": "Chenille Fabric",
            "size": "3 Seater",
        },
    }
    specs = cp.build_product_specs(
        sku_locked="SEB8008",
        variant_locked="Light Chenille Brown",
        campaign_cfg=cfg,
        matched_variant=matched,
    )
    assert specs == (
        "Aurora-Power Sofa Bed "
        "(Color: Light Brown Chenille Fabric/ Size: 3 Seater/ SKU: SEB8008K295)"
    )


def test_parse_fulfillment_address_megan():
    parsed = cp.parse_fulfillment_address(
        "Megan McLeod, 8625 118 Avenue, Grande Prairie, AB T8X 0H4, 587-343-3325",
    )
    assert parsed["full_name"] == "Megan McLeod"
    assert parsed["phone"] == "587-343-3325"
    assert "8625 118 Avenue" in parsed["address"]


def test_enrich_contract_fields_overrides_agent_payload(monkeypatch):
    fields = {
        "date": "2026-06-16",
        "influencer": {
            "full_name": "Megan Allen",
            "email": "theblushhome@outlook.com",
            "instagram": "theblushhome",
            "tiktok": "",
            "youtube": "",
        },
        "product": {
            "specs": "SEB8008 Aurora-Power Sofa Bed - Light Chenille Brown",
            "link": "https://www.povison.com/116-1-electric-sofa-with-usb.html",
        },
        "deliverables": [{"type": "Short Video cross-post"}],
    }
    facts = {
        "offer.sku_locked": "SEB8008",
        "offer.color_or_variant_locked": "Light Chenille Brown",
        "fulfillment.shipping_address": (
            "Megan McLeod, 8625 118 Avenue, Grande Prairie, AB T8X 0H4, 587-343-3325"
        ),
        "identity.primary_handle": "theblushhome",
        "identity.region": "Canada",
    }
    cfg = {
        "label": "Aurora-Power Sofa Bed",
        "product_display_name": "the Aurora-Power smart sofa bed",
        "product_url": (
            "https://www.povison.com/116-1-electric-sofa-with-a-retractable-feature"
            "-in-french-retro-cream-style.html?variant=40300"
        ),
        "campaign_deliverables_json": [],
    }

    def _fake_parse(url: str):
        return [
            {
                "id": "41550",
                "url": (
                    "https://www.povison.com/116-1-electric-sofa-with-a-retractable-feature"
                    "-in-french-retro-cream-style.html?variant=41550"
                ),
                "merchant_sku": "SEB8008K295",
                "attributes": {
                    "size": "3 Seater",
                    "color": "Light Brown Chenille Fabric",
                },
            },
        ]

    monkeypatch.setattr(cp.pv, "parse_variants_from_url", _fake_parse)
    out = cp.enrich_contract_fields(fields, facts=facts, campaign_cfg=cfg)
    assert out["influencer"]["full_name"] == "Megan McLeod"
    assert out["influencer"]["phone"] == "587-343-3325"
    assert "Canada" in out["influencer"]["address"]
    assert out["influencer"]["instagram"] == "https://www.instagram.com/theblushhome"
    assert out["influencer"]["youtube"] == "/"
    assert out["date_long"] == "June 16, 2026"
    assert out["date_short"] == "6/16/2026"
    assert "variant=41550" in out["product"]["link"]
    assert "SEB8008K295" in out["product"]["specs"]
    assert "Light Brown Chenille Fabric" in out["product"]["specs"]
    assert "deliverables" not in out
