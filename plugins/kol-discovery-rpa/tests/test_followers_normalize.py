"""Tests for followers_normalize — locale-specific shorthand normalization."""

from __future__ import annotations

import followers_normalize


def test_english_suffixes():
    assert followers_normalize.normalize_followers("125K") == 125000
    assert followers_normalize.normalize_followers("125k") == 125000
    assert followers_normalize.normalize_followers("1.2M") == 1200000
    assert followers_normalize.normalize_followers("1.2m") == 1200000
    assert followers_normalize.normalize_followers("3.5B") == 3500000000
    assert followers_normalize.normalize_followers("3.5b") == 3500000000


def test_chinese_suffixes():
    assert followers_normalize.normalize_followers("73.8万") == 738000
    assert followers_normalize.normalize_followers("4.6万") == 46000
    assert followers_normalize.normalize_followers("2.3亿") == 230000000
    assert followers_normalize.normalize_followers("10w") == 100000
    assert followers_normalize.normalize_followers("10W") == 100000


def test_plain_numbers():
    assert followers_normalize.normalize_followers("100000") == 100000
    assert followers_normalize.normalize_followers(100000) == 100000
    assert followers_normalize.normalize_followers(125000.0) == 125000


def test_comma_separated():
    assert followers_normalize.normalize_followers("125,000") == 125000
    assert followers_normalize.normalize_followers("1,234,567") == 1234567


def test_edge_cases():
    assert followers_normalize.normalize_followers(None) == 0
    assert followers_normalize.normalize_followers("") == 0
    assert followers_normalize.normalize_followers("invalid") == 0


def test_borderline():
    assert followers_normalize.is_borderline(100000) == True
    assert followers_normalize.is_borderline(105000) == True
    assert followers_normalize.is_borderline(109999) == True
    assert followers_normalize.is_borderline(110000) == False
    assert followers_normalize.is_borderline(99000) == False
    assert followers_normalize.is_borderline(150000) == False
