"""Tests for Gmail reply-all / quoted-reply envelope helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load():
    name = "kol_ops_bridge_gmail_reply_envelope"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _PLUGIN_ROOT / "gmail_reply_envelope.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_reply_all_cc_turnurr_case():
    mod = _load()
    cc = mod.compute_reply_all_cc(
        inbound_from="Alyssa Lopez <alyssa@keyinfluenceragency.com>",
        inbound_to="Candice Wilson <candice@povison-collab.com>",
        inbound_cc="turner@keyinfluenceragency.com",
        reply_to="Alyssa Lopez <alyssa@keyinfluenceragency.com>",
        self_emails={"candice@povison-collab.com"},
    )
    assert "turner@keyinfluenceragency.com" in cc
    assert "candice@povison-collab.com" not in cc.lower()
    assert "alyssa@keyinfluenceragency.com" not in cc.lower()


def test_reply_all_cc_empty_when_no_extra_recipients():
    mod = _load()
    cc = mod.compute_reply_all_cc(
        inbound_from="kol@example.com",
        inbound_to="me@company.com",
        inbound_cc="",
        reply_to="kol@example.com",
        self_emails={"me@company.com"},
    )
    assert cc == ""


def test_body_has_quoted_reply_detects_gmail_container():
    mod = _load()
    assert mod.body_has_quoted_reply('Hi<div class="gmail_quote gmail_quote_container">')
    assert not mod.body_has_quoted_reply("Hi\n\nThanks!")


def test_extract_message_content_without_quotes_strips_nested_thread():
    mod = _load()
    inbound = (
        "Thanks for the details — we'll pass on this one.\n\n"
        "On Thu, Jun 4, 2026 at 3:20 AM Candice wrote:\n"
        "> older offer text\n"
    )
    assert mod.extract_message_content_without_quotes(inbound) == (
        "Thanks for the details — we'll pass on this one."
    )


def test_build_gmail_native_reply_html_matches_web_structure():
    mod = _load()
    parent_html = (
        '<div dir="ltr">Unfortunately we will pass.</div>'
        '<div class="gmail_quote"><blockquote>older turn</blockquote></div>'
    )
    out = mod.build_gmail_native_reply_html(
        new_body="Totally understand.",
        quoted_from="Shay <slevene@viralnation.com>",
        quoted_date="Thu, 4 Jun 2026 04:12:45 -0400",
        quoted_body_html=parent_html,
    )
    assert 'class="gmail_extra"' in out
    assert 'class="gmail_quote"' in out
    assert 'class="gmail_attr"' in out
    assert 'href="mailto:slevene@viralnation.com"' in out
    assert 'type="cite"' in out
    assert "Totally understand." in out
    assert "Unfortunately we will pass." in out
    assert "older turn" in out
    assert "&lt;blockquote" not in out


def test_build_gmail_native_reply_html_falls_back_to_plain():
    mod = _load()
    out = mod.build_gmail_native_reply_html(
        new_body="Hello there.",
        quoted_from="KOL <kol@example.com>",
        quoted_date="Mon, 2 Jun 2026 10:00:00 +0000",
        quoted_body_plain="Prior message line.",
    )
    assert "Prior message line." in out
    assert "Hello there." in out
