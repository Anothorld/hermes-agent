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


def test_append_quoted_reply_plain():
    mod = _load()
    out = mod.append_quoted_reply(
        body="Hello there.",
        quoted_from="KOL <kol@example.com>",
        quoted_date="Mon, 2 Jun 2026 10:00:00 +0000",
        quoted_body="Prior message line.",
    )
    assert out.startswith("Hello there.")
    assert "wrote:" in out
    assert "kol@example.com" in out
    assert "> Prior message line." in out


def test_body_has_quoted_reply_detects_wrote_marker():
    mod = _load()
    assert mod.body_has_quoted_reply("Hi\n\nOn Tue, x wrote:\n> old")
    assert not mod.body_has_quoted_reply("Hi\n\nThanks!")
