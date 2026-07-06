"""Tests for PR1.5: L2 live endpoints (messages/tags/orders/note) with caching."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr15_test"


def _load_pkg_module(sub: str):
    if _PKG not in sys.modules:
        import types

        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal15.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(
        quickcep_session_id="qc-live",
        customer_email="a@b.com",
        message_id="m1",
    )
    # Put a chat_session_id on the row for the note endpoint.
    sess = cal.get_session(quickcep_session_id="qc-live")
    cal.update_session_chat_id(session_row_id=sess["id"], chat_session_id="chat-123")
    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def _headers():
    return {"X-Bridge-Key": "test-key"}


def test_messages_endpoint_returns_filtered(monkeypatch, app):
    live = _load_pkg_module("quickcep_live")
    monkeypatch.setattr(
        live, "fetch_messages",
        lambda *, quickcep_session_id, since: {
            "ok": True, "session_id": quickcep_session_id, "total": 2,
            "count": 1, "messages": [{"id": "m2", "content": "hi"}],
        },
    )
    client = TestClient(app)
    r = client.get("/sessions/qc-live/messages", params={"since": "m1"}, headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["messages"][0]["id"] == "m2"


def test_tags_endpoint_caches(monkeypatch, app):
    live = _load_pkg_module("quickcep_live")
    calls = {"n": 0}

    def fake_fetch(*, quickcep_session_id):
        calls["n"] += 1
        return {"ok": True, "session_id": quickcep_session_id,
                "tagIds": ["t1"], "tags": [{"id": "t1", "name": "AI-草稿待审"}]}

    monkeypatch.setattr(live, "fetch_session_tags", fake_fetch)
    client = TestClient(app)
    r1 = client.get("/sessions/qc-live/tags", headers=_headers())
    r2 = client.get("/sessions/qc-live/tags", headers=_headers())
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["tagIds"] == ["t1"]
    # Caching is inside quickcep_live.fetch_session_tags; since we mocked it at
    # the module level the endpoint calls it directly twice. Verify the real
    # cache logic separately (see test_tags_cache_logic).
    assert calls["n"] == 2


def test_tags_cache_logic(monkeypatch):
    """The real fetch_session_tags caches for TAGS_CACHE_TTL_SEC."""
    live = _load_pkg_module("quickcep_live")
    live._tags_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        return 0, json.dumps({"chatSubSessionId": "qc-x", "tagIds": ["t9"]}), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    monkeypatch.setattr(live, "load_tag_map", lambda: {})
    r1 = live.fetch_session_tags(quickcep_session_id="qc-x")
    r2 = live.fetch_session_tags(quickcep_session_id="qc-x")
    assert r1["tagIds"] == ["t9"]
    assert calls["n"] == 1  # second call served from cache
    # Invalidate -> next call hits CLI again.
    live.invalidate_cache("qc-x")
    r3 = live.fetch_session_tags(quickcep_session_id="qc-x")
    assert calls["n"] == 2
    assert r3["ok"] is True


def test_messages_cache_logic(monkeypatch):
    """fetch_messages caches the full page (since=None) for MESSAGES_CACHE_TTL_SEC."""
    live = _load_pkg_module("quickcep_live")
    live._messages_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        return 0, json.dumps({
            "chatSubSessionId": "qc-m",
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "total": 2,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    r1 = live.fetch_messages(quickcep_session_id="qc-m")
    r2 = live.fetch_messages(quickcep_session_id="qc-m")
    assert r1["ok"] is True
    assert r1["count"] == 2
    assert calls["n"] == 1  # second call served from cache
    # Invalidate -> next call hits CLI again.
    live.invalidate_cache("qc-m")
    r3 = live.fetch_messages(quickcep_session_id="qc-m")
    assert calls["n"] == 2
    assert r3["ok"] is True


def test_messages_cache_since_present_in_cached_page(monkeypatch):
    """When since is present in the cached page, the since filter is applied
    in-memory without a fresh CLI call."""
    live = _load_pkg_module("quickcep_live")
    live._messages_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        return 0, json.dumps({
            "chatSubSessionId": "qc-s",
            "messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
            "total": 3,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    live.fetch_messages(quickcep_session_id="qc-s")  # warm cache
    assert calls["n"] == 1
    r = live.fetch_messages(quickcep_session_id="qc-s", since="m1")
    assert calls["n"] == 1  # served from cache, filtered in-memory
    assert r["count"] == 2
    assert [m["id"] for m in r["messages"]] == ["m2", "m3"]


def test_messages_cache_since_fallback_when_id_missing(monkeypatch):
    """When since is NOT present in the cached page, fall back to a fresh CLI
    call so newer messages are never silently dropped."""
    live = _load_pkg_module("quickcep_live")
    live._messages_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        return 0, json.dumps({
            "chatSubSessionId": "qc-f",
            "messages": [{"id": "m1"}, {"id": "m2"}],
            "total": 2,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    live.fetch_messages(quickcep_session_id="qc-f")  # warm cache with m1, m2
    assert calls["n"] == 1
    # since=m99 is not in the cached page → must fall back to fresh CLI.
    r = live.fetch_messages(quickcep_session_id="qc-f", since="m99")
    assert calls["n"] == 2
    # Loose fallback returns the whole page when since id is absent.
    assert r["count"] == 2
    assert r["ok"] is True


def test_messages_force_fresh_bypasses_cache(monkeypatch):
    """force_fresh=True skips the read cache and refreshes it with the fresh
    result. Used by send_reply to backfill the just-sent message_id."""
    live = _load_pkg_module("quickcep_live")
    live._messages_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        return 0, json.dumps({
            "chatSubSessionId": "qc-ff",
            "messages": [{"id": "m1"}],
            "total": 1,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    live.fetch_messages(quickcep_session_id="qc-ff")  # warm cache
    assert calls["n"] == 1
    r = live.fetch_messages(quickcep_session_id="qc-ff", force_fresh=True)
    assert calls["n"] == 2  # force_fresh bypassed the cache
    assert r["ok"] is True
    # Cache was refreshed by the force_fresh call.
    r2 = live.fetch_messages(quickcep_session_id="qc-ff")
    assert calls["n"] == 2  # now served from refreshed cache


def test_invalidate_cache_clears_messages(monkeypatch):
    """invalidate_cache drops the messages entry too (not just tags/orders)."""
    live = _load_pkg_module("quickcep_live")
    live._tags_cache.clear()
    live._orders_cache.clear()
    live._messages_cache.clear()

    def fake_cli(args):
        return 0, json.dumps({
            "chatSubSessionId": "qc-iv",
            "messages": [{"id": "m1"}],
            "total": 1,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    live.fetch_messages(quickcep_session_id="qc-iv")
    assert "qc-iv" in live._messages_cache
    live.invalidate_cache("qc-iv")
    assert "qc-iv" not in live._messages_cache
    assert "qc-iv" not in live._tags_cache
    assert "qc-iv" not in live._orders_cache


def test_messages_error_not_cached(monkeypatch):
    """A CLI failure must not be cached — the next call retries fresh."""
    live = _load_pkg_module("quickcep_live")
    live._messages_cache.clear()
    calls = {"n": 0}

    def fake_cli(args):
        calls["n"] += 1
        if calls["n"] == 1:
            return 1, "", "boom"
        return 0, json.dumps({
            "chatSubSessionId": "qc-err",
            "messages": [{"id": "m1"}],
            "total": 1,
        }), ""

    monkeypatch.setattr(live, "_run_quickcep_cli", fake_cli)
    r1 = live.fetch_messages(quickcep_session_id="qc-err")
    assert r1["ok"] is False
    assert "qc-err" not in live._messages_cache  # error not cached
    r2 = live.fetch_messages(quickcep_session_id="qc-err")
    assert r2["ok"] is True  # second call retried and succeeded
    assert calls["n"] == 2


def test_orders_endpoint(monkeypatch, app):
    live = _load_pkg_module("quickcep_live")
    monkeypatch.setattr(
        live, "fetch_session_orders",
        lambda *, quickcep_session_id, env="LIVE": {
            "ok": True, "session_id": quickcep_session_id,
            "orders": [{"orderId": "O1"}], "intention_tags": ["物流咨询"], "source": "email_channel",
        },
    )
    client = TestClient(app)
    r = client.get("/sessions/qc-live/orders", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["orders"] == [{"orderId": "O1"}]
    assert body["intention_tags"] == ["物流咨询"]


def test_note_endpoint_adds_note_and_event(monkeypatch, app):
    live = _load_pkg_module("quickcep_live")
    monkeypatch.setattr(
        live, "add_note",
        lambda *, quickcep_session_id, chat_session_id, text: {"ok": True, "stdout": ""},
    )
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-live/note",
        headers=_headers(),
        json={"text": "内部备注内容", "env": "LIVE"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Audit event recorded.
    cal = _load_pkg_module("cal")
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM cs_conversation_events "
            "WHERE event_type='operator_note_added' ORDER BY id DESC LIMIT 1",
        ).fetchone()
    assert row is not None
    assert json.loads(row["payload_json"])["text"] == "内部备注内容"


def test_note_endpoint_requires_chat_session_id(monkeypatch, app, tmp_path):
    """If the session has no chat_session_id and none is supplied, 400."""
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-nochat", customer_email="b@c.com", message_id="m1")
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-nochat/note",
        headers=_headers(),
        json={"text": "x", "env": "LIVE"},
    )
    assert r.status_code == 400


def test_live_endpoints_require_bridge_key(app):
    client = TestClient(app)
    r = client.get("/sessions/qc-live/messages")
    assert r.status_code == 401


def test_live_endpoints_404_unknown_session(app):
    client = TestClient(app)
    r = client.get("/sessions/nope/messages", headers=_headers())
    assert r.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
