from __future__ import annotations

from plugins.povison_product.internal.client import build_lookup_result
from plugins.povison_product.schemas import ParsedProductLink


def test_build_lookup_result_selects_variant_and_maps_options() -> None:
    parsed = ParsedProductLink(
        original_url="https://www.povison.com/products/tv-stand-43956.html?variant=43962",
        host="www.povison.com",
        path="products/tv-stand-43956.html",
        variant="43962",
    )
    payload = {
        "code": 200,
        "msg": "success",
        "info": {
            "productDetailVOList": [
                {
                    "spuInfo": {"spu": "M2-TS8319", "spuId": 43956, "entityId": 43956, "name": "Mid-Century Modern TV Stand"},
                    "saleAttr": [
                        {
                            "attributeCode": "size",
                            "attributeLabel": "Size",
                            "valueList": [{"valueId": 24984, "value": "240 x 56 x 56 cm", "enValue": "94.49 x 22.05 x 22.05 inch"}],
                        }
                    ],
                    "skuList": [
                        {
                            "entityId": 43962,
                            "sku": "TS8319WC240",
                            "name": "240cm Mid-Century Modern TV Stand with storage,walnut",
                            "productName": "Arboren-240cm Mid-Century Modern TV Stand with storage,walnut",
                            "detailUrl": "products/tv-stand-43956.html?variant=43962",
                            "price": 2299.0,
                            "specialPrice": 1999.0,
                            "saleValueList": [
                                {"attributeCode": "color", "attributeLabel": "Color", "value": "Walnut Color"},
                                {"attributeCode": "size", "attributeLabel": "Size", "value": "240 x 56 x 56 cm"},
                            ],
                        }
                    ],
                }
            ]
        },
    }

    result = build_lookup_result(parsed, payload)

    assert result["request"] == {
        "url": parsed.original_url,
        "path": "products/tv-stand-43956.html",
        "variant": "43962",
        "storeId": 3,
    }
    assert result["product"]["spu"] == "M2-TS8319"
    assert result["selected_variant_found"] is True
    assert result["selected_sku"]["sku"] == "TS8319WC240"
    assert result["selected_sku"]["detail_url"] == "https://www.povison.com/products/tv-stand-43956.html?variant=43962"
    assert result["selected_sku"]["options"] == {"color": "Walnut Color", "size": "240 x 56 x 56 cm"}
    assert result["option_groups"][0]["values"][0]["en_value"] == "94.49 x 22.05 x 22.05 inch"
