"""Tests for the kol-discovery-precompress-guard plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_hooks():
    hooks_path = PLUGIN_ROOT / "hooks.py"
    module_name = "test_kol_discovery_precompress_guard.hooks"
    spec = importlib.util.spec_from_file_location(module_name, hooks_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Session gating
# ---------------------------------------------------------------------------

def test_discovery_session_detected():
    h = _load_hooks()
    assert h._is_discovery_session("kol-campaign:LIVE:SSF8033-20260609")
    assert h._is_discovery_session("kol-campaign:TEST:SEB8010-20260608")


def test_non_discovery_sessions_rejected():
    h = _load_hooks()
    assert not h._is_discovery_session("kol-campaign-outreach:LIVE:POVISON-TS-8319")
    assert not h._is_discovery_session("kol-campaign-draft:LIVE:SEB8008-20260525:820")
    assert not h._is_discovery_session("kol-campaign-reply:LIVE:923")
    assert not h._is_discovery_session("kol-email-discover:LIVE:42")
    assert not h._is_discovery_session("")
    assert not h._is_discovery_session("default")


# ---------------------------------------------------------------------------
# URL / handle extraction
# ---------------------------------------------------------------------------

def test_extract_handle_from_profile_url():
    h = _load_hooks()
    assert h._extract_handle_from_url("https://www.instagram.com/sydneynicoleslone/") == "sydneynicoleslone"
    assert h._extract_handle_from_url("https://instagram.com/makeitwithmicah") == "makeitwithmicah"
    assert h._extract_handle_from_url("https://www.instagram.com/ready.set.dad/") == "ready.set.dad"


def test_extract_handle_skips_non_profile_paths():
    h = _load_hooks()
    assert h._extract_handle_from_url("https://www.instagram.com/explore/tags/cozyliving/") is None
    assert h._extract_handle_from_url("https://www.instagram.com/reel/ABC123/") is None
    assert h._extract_handle_from_url("https://www.instagram.com/p/XYZ/") is None
    assert h._extract_handle_from_url("https://www.google.com/search?q=foo") is None
    assert h._extract_handle_from_url("") is None


# ---------------------------------------------------------------------------
# Message walking
# ---------------------------------------------------------------------------

def _assistant_msg_with_tool_call(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "tc-1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def _tool_result_msg(text: str) -> dict:
    return {"role": "tool", "tool_call_id": "tc-1", "content": text}


def test_collect_visited_handles_dedupes_preserving_order():
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/alpha/"},
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/beta/"},
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/alpha/reels/"},  # /reels/ still matches handle
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/explore/tags/cozyliving/"},  # not a profile
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.google.com/search?q=foo"},  # not IG
        ),
    ]
    visited = h.collect_visited_handles(messages)
    assert visited == ["alpha", "beta"]


def test_collect_visited_handles_includes_rpa_tools():
    """rpa_fetch_ig_profile and rpa_fetch_ig_reels count as visited."""
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "rpa_fetch_ig_profile",
            {"handle": "rpa_alpha"},
        ),
        _assistant_msg_with_tool_call(
            "rpa_fetch_ig_reels",
            {"handle": "rpa_beta"},
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/browser_gamma/"},
        ),
        # Same handle via rpa + browser → deduped
        _assistant_msg_with_tool_call(
            "rpa_fetch_ig_profile",
            {"handle": "browser_gamma"},
        ),
    ]
    visited = h.collect_visited_handles(messages)
    assert visited == ["rpa_alpha", "rpa_beta", "browser_gamma"]


def test_collect_visited_handles_rpa_handle_with_at_prefix():
    """RPA handle with @ prefix is normalized."""
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "rpa_fetch_ig_profile",
            {"handle": "@username"},
        ),
    ]
    visited = h.collect_visited_handles(messages)
    assert visited == ["username"]


def test_compute_pending_handles_subtracts_ingested(tmp_path, monkeypatch):
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/ingested_one/"},
        ),
        _tool_result_msg(
            '{"candidate_id": 480, "identity_id": 929, '
            '"primary_handle": "ingested_one", "ok": true}'
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/pending_one/"},
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/pending_two/"},
        ),
    ]
    pending = h.compute_pending_handles(messages)
    assert pending == ["pending_one", "pending_two"]


def test_compute_pending_handles_empty_when_all_ingested():
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/done/"},
        ),
        _tool_result_msg(
            '{"candidate_id": 1, "identity_id": 7, '
            '"primary_handle": "done", "ok": true}'
        ),
    ]
    assert h.compute_pending_handles(messages) == []


def test_compute_pending_handles_does_not_false_positive_on_failed_ingest():
    """A 400/422 ingest error must NOT be treated as a successful ingest —
    the handle should remain in the pending snapshot so the next round
    can retry it. Regression guard for the tightened ``_INGEST_SUCCESS_RE``
    (formerly matched ``ingest-confirmed-candidate.*?identity_id`` which
    false-positived on validation errors mentioning identity_id).
    """
    h = _load_hooks()
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/failed_one/"},
        ),
        _tool_result_msg(
            '{"error": "http_error", "status": 400, '
            '"detail": "{\\"detail\\":\\"identity_id mapping required\\"}", '
            '"path": "/campaigns/CID/ingest-confirmed-candidate"}'
        ),
    ]
    pending = h.compute_pending_handles(messages)
    assert pending == ["failed_one"], (
        "a failed ingest must keep the handle in the pending snapshot"
    )


def test_compute_pending_handles_empty_when_no_visits():
    h = _load_hooks()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert h.compute_pending_handles(messages) == []


# ---------------------------------------------------------------------------
# pre_compress hook end-to-end (filesystem side effect)
# ---------------------------------------------------------------------------

def test_pre_compress_writes_snapshot_for_discovery_session(tmp_path, monkeypatch):
    h = _load_hooks()
    monkeypatch.setattr(h.tempfile, "gettempdir", lambda: str(tmp_path))
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/selenayazs/"},
        ),
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/styledbybeck/"},
        ),
    ]
    result = h.pre_compress(
        session_id="kol-campaign:LIVE:SSF8033-20260609",
        task_id="",
        messages=messages,
    )
    assert result is None  # side-effect only

    snap = tmp_path / "precompress_pending_kol-campaign_LIVE_SSF8033-20260609.json"
    assert snap.exists()
    payload = json.loads(snap.read_text())
    assert payload["session_id"] == "kol-campaign:LIVE:SSF8033-20260609"
    assert payload["count"] == 2
    assert payload["pending_handles"] == ["selenayazs", "styledbybeck"]


def test_pre_compress_skips_non_discovery_session(tmp_path, monkeypatch):
    h = _load_hooks()
    monkeypatch.setattr(h.tempfile, "gettempdir", lambda: str(tmp_path))
    messages = [
        _assistant_msg_with_tool_call(
            "browser_navigate",
            {"url": "https://www.instagram.com/should_be_ignored/"},
        ),
    ]
    h.pre_compress(
        session_id="kol-campaign-outreach:LIVE:POVISON-TS-8319",
        messages=messages,
    )
    # No snapshot written for outreach sessions.
    assert list(tmp_path.glob("precompress_pending_*")) == []


def test_pre_compress_no_snapshot_when_no_pending(tmp_path, monkeypatch):
    h = _load_hooks()
    monkeypatch.setattr(h.tempfile, "gettempdir", lambda: str(tmp_path))
    h.pre_compress(
        session_id="kol-campaign:LIVE:SSF8033-20260609",
        messages=[],  # no visits
    )
    assert list(tmp_path.glob("precompress_pending_*")) == []


def test_pre_compress_never_raises_on_bad_messages(tmp_path, monkeypatch):
    h = _load_hooks()
    monkeypatch.setattr(h.tempfile, "gettempdir", lambda: str(tmp_path))
    # Garbage input must not propagate.
    result = h.pre_compress(
        session_id="kol-campaign:LIVE:SSF8033-20260609",
        messages="not-a-list",  # type: ignore[arg-type]
    )
    assert result is None
