"""Tests for creator CLI arg builder."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.creator_args import (  # noqa: E402
    build_creator_read_args,
    needs_direct_creator_http,
    prefer_cli_selector_over_dash_id,
)
from internal.creator_http import build_creator_api_path  # noqa: E402


def test_dash_id_with_url_uses_url_selector():
    cid, url, _, _ = prefer_cli_selector_over_dash_id(
        "-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw",
        "https://instagram.com/foo",
        None,
        None,
    )
    assert cid is None
    assert url == "https://instagram.com/foo"
    args = build_creator_read_args(
        "audience",
        creator_id="-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw",
        url="https://instagram.com/foo",
        platform=None,
        channel_id=None,
        detail=True,
    )
    assert args == [
        "audience",
        "--url",
        "https://instagram.com/foo",
        "--detail",
    ]
    assert not needs_direct_creator_http(
        "-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw",
        "https://instagram.com/foo",
        None,
        None,
    )


def test_dash_id_without_selector_needs_http():
    assert needs_direct_creator_http("-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw", None, None, None)
    path = build_creator_api_path(
        "audience",
        creator_id="-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw",
        detail=True,
    )
    assert path.startswith("/api/v1/creators/")
    assert "-8rHiVyuP0ptLyiuQCu-pqpLTmYXh5uX-dw" in path
    assert path.endswith("/audience/detail")


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
