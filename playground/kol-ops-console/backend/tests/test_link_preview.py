"""Tests for Open Graph link preview parsing."""

from app.link_preview import _host_allowed, _parse_og_tags


def test_host_allowed_instagram():
    assert _host_allowed("https://www.instagram.com/foo/")


def test_host_allowed_rejects_random():
    assert not _host_allowed("https://evil.example/phish")


def test_parse_og_tags():
    html = """
    <html><head>
    <meta property="og:title" content="Test (@user)" />
    <meta content="https://cdn.example/avatar.jpg" property="og:image" />
    <meta property="og:description" content="1M Followers" />
    </head></html>
    """
    tags = _parse_og_tags(html)
    assert tags["og:title"] == "Test (@user)"
    assert tags["og:image"] == "https://cdn.example/avatar.jpg"
    assert "1M" in tags["og:description"]
