"""Unit tests for the outbound-draft HTML normalizer (POVISON 683 guard)."""

from __future__ import annotations


def test_plain_paragraphs_become_p_tags(bridge_pkg):
    f = bridge_pkg.reply_draft.to_html_email_body
    out = f("Hi McKenna,\n\nWe love your work.\n\nBest,\nPOVISON Team")
    assert out.count("<p>") == 3
    assert "<br>" in out  # single newline inside a paragraph
    assert out.startswith("<p>Hi McKenna,</p>")


def test_bare_url_is_linkified(bridge_pkg):
    f = bridge_pkg.reply_draft.to_html_email_body
    out = f("See https://www.povison.com/x?a=1&b=2 now")
    assert '<a href="https://www.povison.com/x?a=1&amp;b=2">' in out
    assert "https://www.povison.com/x?a=1&amp;b=2</a>" in out


def test_existing_html_is_idempotent(bridge_pkg):
    f = bridge_pkg.reply_draft.to_html_email_body
    html = '<p>Hi Mary,</p><p>We launched <a href="https://x.com">it</a>.</p>'
    assert f(html) == html


def test_empty_body_returns_empty(bridge_pkg):
    f = bridge_pkg.reply_draft.to_html_email_body
    assert f("") == ""
    assert f(None) == ""
    assert f("   \n  ") == ""


def test_text_is_escaped(bridge_pkg):
    f = bridge_pkg.reply_draft.to_html_email_body
    out = f("5 < 6 & a > b")
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out
    assert "<p>" in out  # tags we added are real, content is escaped
