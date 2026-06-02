"""Tests for email body normalization and edit distance."""

from __future__ import annotations


def test_identical_bodies_zero_distance(bridge_pkg):
    rd = bridge_pkg.reply_diff
    payload = rd.build_edit_learning_payload(
        agent_body="Hi there,\n\nThanks for your reply.",
        sent_body="Hi there,\n\nThanks for your reply.",
        child_skill="kol-compensation-negotiator",
        goal="compensation_negotiation",
    )
    assert payload["edit_distance"] == 0.0
    assert payload["was_edited"] is False


def test_edited_body_nonzero_distance(bridge_pkg):
    rd = bridge_pkg.reply_diff
    payload = rd.build_edit_learning_payload(
        agent_body="Hi there,\n\nWe can offer $1200.",
        sent_body="Hi,\n\nWe can offer $1000.",
        child_skill="kol-compensation-negotiator",
        goal="compensation_negotiation",
    )
    assert payload["edit_distance"] > 0.05
    assert payload["was_edited"] is True


def test_strip_html_and_quotes(bridge_pkg):
    rd = bridge_pkg.reply_diff
    html = "<p>Hello</p><br/>World"
    assert rd.normalize_email_body(html) == "Hello World"
