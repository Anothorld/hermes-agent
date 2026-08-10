"""Unit tests for serp-api providers + cache (HTTP mocked, no network)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the plugin importable without installing it.
_PLUGIN = Path(__file__).resolve().parents[1]
_INTERNAL = _PLUGIN / "internal"
# Only internal/ is needed (cache/client/providers are top-level modules there).
# Avoid adding the plugin dir itself — its hyphenated name + __init__ confuse pytest.
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

import cache as cache_mod  # noqa: E402
import client as client_mod  # noqa: E402
import providers as prov_mod  # noqa: E402


# --------------------------------------------------------------------- providers


def _norm(item):
    """Normalize a result row for assertion (drop provider-specific noise)."""
    return {k: item[k] for k in ("rank", "title", "url", "snippet")}


def test_google_cse_normalizes_items(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "K")
    monkeypatch.setenv("GOOGLE_CSE_CX", "CX")
    payload = {"items": [
        {"title": "A", "link": "http://a", "snippet": "sa"},
        {"title": "B", "link": "http://b", "snippet": "sb"},
    ]}
    with patch.object(client_mod, "_request", return_value=payload):
        out = prov_mod._google_cse("q", 10, "us", "en")
    assert out["count"] == 2
    assert out["provider"] == "google_cse"
    assert [_norm(r) for r in out["results"]] == [
        {"rank": 1, "title": "A", "url": "http://a", "snippet": "sa"},
        {"rank": 2, "title": "B", "url": "http://b", "snippet": "sb"},
    ]


def test_serper_normalizes_organic(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "K")
    payload = {"organic": [{"title": "T", "link": "http://t", "snippet": "s"}]}
    with patch.object(client_mod, "_request", return_value=payload):
        out = prov_mod._serper("q", 20, "us", "en")
    assert out["count"] == 1 and out["results"][0]["rank"] == 1


def test_serpapi_normalizes_organic_results(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "K")
    payload = {"organic_results": [{"title": "T", "link": "http://t", "snippet": "s"}]}
    with patch.object(client_mod, "_request", return_value=payload):
        out = prov_mod._serpapi("q", 20, "us", "en")
    assert out["count"] == 1 and out["provider"] == "serpapi"


def test_valueserp_normalizes_organic_results(monkeypatch):
    monkeypatch.setenv("VALUESERP_KEY", "K")
    payload = {"organic_results": [{"title": "T", "link": "http://t", "snippet": "s"}]}
    with patch.object(client_mod, "_request", return_value=payload):
        out = prov_mod._valueserp("q", 20, "us", "en")
    assert out["count"] == 1 and out["provider"] == "valueserp"


def test_resolve_provider_defaults_to_google_cse(monkeypatch):
    monkeypatch.delenv("SERP_API_PROVIDER", raising=False)
    assert prov_mod.resolve_provider() == "google_cse"


def test_is_configured_requires_cx_for_google_cse(monkeypatch):
    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "K")
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    assert prov_mod.is_configured("google_cse") is False
    monkeypatch.setenv("GOOGLE_CSE_CX", "CX")
    assert prov_mod.is_configured("google_cse") is True


def test_generic_serp_api_key_fallback(monkeypatch):
    monkeypatch.setenv("SERP_API_KEY", "GENERIC")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert prov_mod._key("serper") == "GENERIC"


def test_fetch_dispatches_to_configured_provider(monkeypatch):
    monkeypatch.setenv("SERP_API_PROVIDER", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "K")
    payload = {"organic": []}
    with patch.object(client_mod, "_request", return_value=payload):
        out = prov_mod.fetch("q", 10, "us", "en")
    assert out["provider"] == "serper"


def test_wigolo_normalizes_results_no_key(monkeypatch):
    monkeypatch.setenv("WIGOLO_API_URL", "http://127.0.0.1:3333")
    monkeypatch.delenv("WIGOLO_API_TOKEN", raising=False)
    payload = {"results": [
        {"title": "A", "url": "http://a", "snippet": "sa", "relevance_score": 0.9},
        {"title": "B", "url": "http://b", "snippet": "sb", "relevance_score": 0.8},
    ], "engines_used": ["bing", "duckduckgo"]}
    with patch.object(client_mod, "_request", return_value=payload) as mock_req:
        out = prov_mod._wigolo("q", 10, "us", "en")
    assert out["count"] == 2
    assert out["provider"] == "wigolo"
    assert out["engines_used"] == ["bing", "duckduckgo"]
    assert [_norm(r) for r in out["results"]] == [
        {"rank": 1, "title": "A", "url": "http://a", "snippet": "sa"},
        {"rank": 2, "title": "B", "url": "http://b", "snippet": "sb"},
    ]
    # wigolo needs no key → no Authorization header by default.
    _url, _method, _body, headers = mock_req.call_args.args
    assert "Authorization" not in headers


def test_wigolo_sends_bearer_token_when_set(monkeypatch):
    monkeypatch.setenv("WIGOLO_API_URL", "http://127.0.0.1:3333")
    monkeypatch.setenv("WIGOLO_API_TOKEN", "tok")
    with patch.object(client_mod, "_request", return_value={"results": []}) as mock_req:
        prov_mod._wigolo("q", 10, "us", "en")
    _url, _method, _body, headers = mock_req.call_args.args
    assert headers["Authorization"] == "Bearer tok"


def test_wigolo_is_configured_with_url_only(monkeypatch):
    monkeypatch.delenv("WIGOLO_API_TOKEN", raising=False)
    monkeypatch.setenv("WIGOLO_API_URL", "http://127.0.0.1:3333")
    assert prov_mod.is_configured("wigolo") is True
    monkeypatch.delenv("WIGOLO_API_URL", raising=False)
    # Falls back to the default loopback URL → still configured.
    assert prov_mod.is_configured("wigolo") is True


def test_brave_normalizes_results_and_strips_html(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BK")
    payload = {"web": {"results": [
        {"title": "A", "url": "http://a", "description": "Best <strong>pet</strong> sofa"},
        {"title": "B", "url": "http://b", "description": "no tags here"},
    ]}}
    with patch.object(client_mod, "_request", return_value=payload) as mock_req:
        out = prov_mod._brave("q", 10, "us", "en")
    assert out["count"] == 2
    assert out["provider"] == "brave"
    assert [_norm(r) for r in out["results"]] == [
        {"rank": 1, "title": "A", "url": "http://a", "snippet": "Best pet sofa"},
        {"rank": 2, "title": "B", "url": "http://b", "snippet": "no tags here"},
    ]
    # X-Subscription-Token header carried through.
    _url, _method, _body, headers = mock_req.call_args.args
    assert headers["X-Subscription-Token"] == "BK"


def test_brave_key_resolution_fallbacks_to_serper_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("SERP_API_KEY", "GEN")
    with patch.object(client_mod, "_request", return_value={"web": {"results": []}}) as mock_req:
        prov_mod._brave("q", 10, "us", "en")
    _url, _method, _body, headers = mock_req.call_args.args
    assert headers["X-Subscription-Token"] == "GEN"


def test_brave_is_configured_needs_key(monkeypatch):
    for v in ("BRAVE_SEARCH_API_KEY", "BRAVE_API_KEY", "SERP_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert prov_mod.is_configured("brave") is False
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BK")
    assert prov_mod.is_configured("brave") is True


# ------------------------------------------------------------------------- cache


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SERP_API_CACHE_DIR", str(tmp_path))
    assert cache_mod.cache_get("google_cse", "q", "us", "en", 60) is None
    payload = {"results": [{"rank": 1}], "count": 1}
    cache_mod.cache_put("google_cse", "q", "us", "en", payload)
    assert cache_mod.cache_get("google_cse", "q", "us", "en", 60) == payload


def test_cache_ttl_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("SERP_API_CACHE_DIR", str(tmp_path))
    cache_mod.cache_put("google_cse", "q", "us", "en", {"x": 1})
    # ttl=0 means "always miss" per the cache contract.
    assert cache_mod.cache_get("google_cse", "q", "us", "en", 0) is None


def test_cache_key_case_insensitive_query(tmp_path, monkeypatch):
    monkeypatch.setenv("SERP_API_CACHE_DIR", str(tmp_path))
    cache_mod.cache_put("google_cse", "Query", "us", "en", {"x": 1})
    assert cache_mod.cache_get("google_cse", "query", "us", "en", 60) == {"x": 1}


# ---------------------------------------------------------------- client retry


def test_client_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("SERP_API_MAX_RETRIES", "2")
    calls = {"n": 0}

    def fake_request(url, method, body, headers):
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return {"items": []}

    with patch.object(client_mod, "_request", side_effect=fake_request):
        data = client_mod.http_get_json("https://x", {"q": "z"}, provider="google_cse")
    assert data == {"items": []}
    assert calls["n"] == 2


def test_client_circuit_opens_after_threshold(monkeypatch):
    monkeypatch.setenv("SERP_API_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("SERP_API_BREAKER_RESET_S", "60")
    client_mod._fail_streak = 0
    client_mod._open_until = 0.0

    def boom(url, method, body, headers):
        raise urllib.error.HTTPError(url, 500, "err", {}, None)

    with patch.object(client_mod, "_request", side_effect=boom):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                client_mod.http_get_json("https://x", {"q": "z"}, provider="google_cse")
    # Third call should be rejected by the open breaker (not even hit the network).
    with pytest.raises(RuntimeError, match="circuit breaker open"):
        client_mod.http_get_json("https://x", {"q": "z"}, provider="google_cse")
