"""Tests for creator CLI arg builder."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.creator_args import build_creator_read_args  # noqa: E402


def test_id_precludes_url():
    args = build_creator_read_args(
        "audience",
        creator_id="cid123",
        url="https://youtube.com/@x",
        platform="youtube",
        channel_id=None,
        detail=True,
    )
    assert args == ["audience", "cid123", "--detail"]
    assert "--url" not in args


def test_url_when_no_id():
    args = build_creator_read_args(
        "profile",
        creator_id=None,
        url="https://youtube.com/@x",
        platform="youtube",
        channel_id=None,
        detail=False,
    )
    assert args == ["profile", "--url", "https://youtube.com/@x"]
