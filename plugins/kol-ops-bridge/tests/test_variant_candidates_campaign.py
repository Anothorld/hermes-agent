"""Campaign variant_candidates persistence in CAL."""


def test_upsert_and_read_variant_candidates(cal_db):
    cal_db.upsert_campaign_config(
        campaign_id="VAR-1",
        env="TEST",
        label="Sofa drop",
        product_display_name="Cloud Sofa",
        sku_whitelist=["37384", "35590"],
        variant_candidates=[
            {
                "id": "37384",
                "label": "Green / Suede",
                "url": "https://example.com?variant=37384",
                "attributes": {"color": "Green"},
                "price": 1999.0,
            },
            {
                "id": "35590",
                "label": "Beige / Suede",
                "url": "https://example.com?variant=35590",
                "attributes": {"color": "Beige"},
            },
        ],
        color_variant_policy="operator_selected: Green / Suede | Beige / Suede",
    )
    cfg = cal_db.get_campaign_config("VAR-1", env="TEST")
    assert cfg is not None
    assert cfg["sku_whitelist"] == ["37384", "35590"]
    assert len(cfg["variant_candidates"]) == 2
    assert cfg["variant_candidates"][0]["price"] == 1999.0
