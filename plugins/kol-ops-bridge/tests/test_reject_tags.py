"""Tests for reject tag normalization."""

from __future__ import annotations


def test_normalize_empty_defaults_to_other(bridge_pkg):
    rt = bridge_pkg.reject_tags
    assert rt.normalize_reject_tags(None) == ["other"]
    assert rt.normalize_reject_tags([]) == ["other"]


def test_normalize_deduplicates_and_maps_unknown(bridge_pkg):
    rt = bridge_pkg.reject_tags
    out = rt.normalize_reject_tags(["tone_too_salesy", "bogus", "tone_too_salesy"])
    assert out == ["tone_too_salesy", "other"]
