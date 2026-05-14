"""HTTP transport tests using httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from plugins.fb_creator_discovery._internal.credentials import FBCredentials
from plugins.fb_creator_discovery._internal.errors import (
    FBCreatorAPIError,
    FBCreatorAuthRequiredError,
)
from plugins.fb_creator_discovery._internal.http import FBGraphHTTPClient


class StaticProvider:
    def __init__(self, token: str = "test-token", version: str = "v21.0") -> None:
        self._creds = FBCredentials(page_access_token=token, api_version=version)

    def resolve(self) -> FBCredentials:
        return self._creds

    def is_configured(self) -> bool:
        return True


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> FBGraphHTTPClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return FBGraphHTTPClient(StaticProvider(), http=http)


def test_get_success_returns_parsed_json_and_injects_token():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [{"creator_id": "1"}]})

    client = _make_client(handler)
    payload = client.get(
        "/creator_marketplace/creators",
        params={"limit": 10, "after": None},
    )

    assert payload == {"data": [{"creator_id": "1"}]}
    assert captured["url"].startswith(
        "https://graph.facebook.com/v21.0/creator_marketplace/creators"
    )
    assert captured["params"]["access_token"] == "test-token"
    assert captured["params"]["limit"] == "10"
    assert "after" not in captured["params"]  # None values dropped


def test_401_raises_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 190, "message": "expired"}})

    client = _make_client(handler)
    with pytest.raises(FBCreatorAuthRequiredError):
        client.get("/creator_marketplace/creators")


def test_rate_limit_code_4_raises_api_error_with_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "120"},
            json={
                "error": {
                    "code": 4,
                    "error_subcode": 1349174,
                    "message": "App request limit reached",
                    "fbtrace_id": "trace-xyz",
                }
            },
        )

    client = _make_client(handler)
    with pytest.raises(FBCreatorAPIError) as exc_info:
        client.get("/creator_marketplace/creators")
    err = exc_info.value
    assert err.status_code == 429
    assert err.fb_error_code == 4
    assert err.fb_error_subcode == 1349174
    assert err.fb_trace_id == "trace-xyz"
    assert err.retry_after == "120"


def test_generic_400_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": 100, "message": "bad param"}}
        )

    client = _make_client(handler)
    with pytest.raises(FBCreatorAPIError) as exc_info:
        client.get("/creator_marketplace/creators")
    assert exc_info.value.status_code == 400
    assert exc_info.value.fb_error_code == 100


def test_non_json_body_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    client = _make_client(handler)
    with pytest.raises(FBCreatorAPIError):
        client.get("/creator_marketplace/creators")


def test_url_uses_configured_api_version():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = FBGraphHTTPClient(StaticProvider(version="v22.0"), http=http)
    client.get("/creator_marketplace/content")
    assert "/v22.0/creator_marketplace/content" in captured["url"]
