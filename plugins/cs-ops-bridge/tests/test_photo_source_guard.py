"""Tests for photo_source_guard — blocks hotlinked static.povison.com <img> tags."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_photo_guard_test"


def _load_module(sub: str):
    if _PKG not in sys.modules:
        import types

        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


@pytest.fixture()
def guard_module():
    return _load_module("photo_source_guard")


# ── The exact draft that was sent to a.khoroushi@gmail.com (session 2557794283807629320) ──

REAL_BAD_DRAFT = """<html>
<body>
<p>Here are the product images of the TS8266WC180 for your reference:</p>
<p><img src="https://static.povison.com/media/catalog/product/tv/stands/20260629/2026062916353265629827.jpg" alt="TS8266WC180 Main Image" width="600" /></p>
<p><img src="https://static.povison.com/media/catalog/product/tv/stands/20260629/2026062916355122243488.jpg" alt="TS8266WC180 Scene Image" width="600" /></p>
<p>You can view the full product page here: <a href="https://www.povison.com/some-product.html">Product Page</a></p>
</body>
</html>"""


def test_blocks_real_bad_draft(guard_module):
    """The exact draft from session 2557794283807629320 must be blocked."""
    result = guard_module.guard_draft(REAL_BAD_DRAFT)
    assert result["blocked"] is True
    assert "static.povison.com" in result["error_detail"]
    assert len(result["matches"]) >= 2


def test_blocks_single_static_povison_img(guard_module):
    result = guard_module.guard_draft(
        '<p><img src="https://static.povison.com/media/catalog/product/foo.jpg" /></p>'
    )
    assert result["blocked"] is True


def test_allows_quickcep_cdn_images(guard_module):
    """Images re-uploaded to QuickCEP CDN are fine (QC photos, operator uploads)."""
    result = guard_module.guard_draft(
        '<p><img src="https://quick-cep-cdn.quickcep.com/3371/message-center/im/mail/123/abc.png" /></p>'
    )
    assert result["blocked"] is False


def test_allows_aliyun_oss_images(guard_module):
    """Direct QC CDN (aliyun OSS) images are fine."""
    result = guard_module.guard_draft(
        '<img src="http://musem-scm-public.oss-cn-guangzhou.aliyuncs.com/srm/qc/20260603/abc.jpg" />'
    )
    assert result["blocked"] is False


def test_allows_product_page_links(guard_module):
    """<a href> to povison.com product pages is fine — only <img src> is blocked."""
    result = guard_module.guard_draft(
        '<p>See: <a href="https://www.povison.com/some-product.html">Product Page</a></p>'
    )
    assert result["blocked"] is False


def test_allows_plain_text(guard_module):
    result = guard_module.guard_draft("<p>Hello, no images here.</p>")
    assert result["blocked"] is False


def test_allows_empty_content(guard_module):
    result = guard_module.guard_draft("")
    assert result["blocked"] is False


def test_case_insensitive_domain(guard_module):
    result = guard_module.guard_draft(
        '<img src="https://STATIC.Povison.com/media/catalog/product/foo.jpg" />'
    )
    assert result["blocked"] is True


def test_blocks_with_extra_attributes(guard_module):
    """Handles <img> with many attributes before/after src."""
    result = guard_module.guard_draft(
        '<img class="responsive" data-id="42" src="https://static.povison.com/media/x.jpg" '
        'alt="product" width="600" height="400" />'
    )
    assert result["blocked"] is True
