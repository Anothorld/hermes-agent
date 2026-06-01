from __future__ import annotations

from app.product_variants import parse_variants_from_url


def test_parse_variant_from_generic_query_only_url():
    out = parse_variants_from_url(
        "https://shop.example/widget?variant=12345&color=red&size=L"
    )
    assert len(out) == 1
    assert out[0]["id"] == "12345"
    assert out[0]["attributes"] == {"color": "red", "size": "L"}


def test_parse_povison_all_variants(monkeypatch):
    def _fake_fetch(path: str, variant: str | None):
        assert path == "foo-product.html"
        assert variant == "37384"
        return [
            {
                "entityId": 35590,
                "sku": "SF8181E265",
                "detailUrl": "foo-product.html?variant=35590",
                "saleValueList": [
                    {"attributeCode": "size", "value": "2 Armless Chair+1 Armrest"},
                    {"attributeCode": "material", "value": "Suede Fabric"},
                    {"attributeCode": "color", "value": "Beige"},
                ],
            },
            {
                "entityId": 37384,
                "sku": "SF8181G265",
                "detailUrl": "foo-product.html?variant=37384",
                "saleValueList": [
                    {"attributeCode": "size", "value": "2 Armless Chair+2 Armrest"},
                    {"attributeCode": "material", "value": "Suede Fabric"},
                    {"attributeCode": "color", "value": "Green"},
                ],
            },
        ]

    monkeypatch.setattr(
        "app.product_variants._fetch_povison_sku_list",
        _fake_fetch,
    )
    out = parse_variants_from_url(
        "https://www.povison.com/foo-product.html?variant=37384"
    )
    assert [v["id"] for v in out] == ["35590", "37384"]
    assert out[0]["url"] == "https://www.povison.com/foo-product.html?variant=35590"
    assert out[1]["attributes"]["color"] == "Green"
    assert "Green" in (out[1]["label"] or "")


def test_parse_povison_falls_back_to_query_variant_when_api_empty(monkeypatch):
    monkeypatch.setattr("app.product_variants._fetch_povison_sku_list", lambda **_: [])
    out = parse_variants_from_url(
        "https://www.povison.com/foo-product.html?variant=43497&color=gray"
    )
    assert len(out) == 1
    assert out[0]["id"] == "43497"
    assert out[0]["attributes"] == {"color": "gray"}
