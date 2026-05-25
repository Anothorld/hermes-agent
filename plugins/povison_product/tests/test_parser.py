from __future__ import annotations

import pytest

from plugins.povison_product.internal.errors import PovisonParseError
from plugins.povison_product.internal.parser import parse_product_link


def test_parse_products_path_preserves_directory() -> None:
    parsed = parse_product_link("https://www.povison.com/products/tv-stand-43956.html?variant=43962")

    assert parsed.path == "products/tv-stand-43956.html"
    assert parsed.variant == "43962"
    assert parsed.store_id == 3


def test_parse_root_product_path() -> None:
    parsed = parse_product_link(
        "https://www.povison.com/sailboat-sofa-with-adjustable-backrest-dog-proof-couch-anti-scratch-and-water-proof-fabric.html?variant=43497"
    )

    assert parsed.path == "sailboat-sofa-with-adjustable-backrest-dog-proof-couch-anti-scratch-and-water-proof-fabric.html"
    assert parsed.variant == "43497"


def test_parse_requires_variant() -> None:
    with pytest.raises(PovisonParseError):
        parse_product_link("https://www.povison.com/products/tv-stand-43956.html")
