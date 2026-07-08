"""Tests for GET /config/intent-scope."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


@pytest.fixture
def client():
    from cs_intent_classifier_pkg import plugin_api  # type: ignore[attr-defined]

    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def test_intent_scope_config_returns_whitelist(client):
    r = client.get("/config/intent-scope")
    assert r.status_code == 200
    data = r.json()
    scope = data["scope"]
    assert scope["product_inquiry"] is True
    assert scope["logistics_inquiry"] is True
    assert scope["after_sale_issue"] is False
    assert "product_inquiry" in data["in_scope_intents"]
    assert "after_sale_issue" in data["out_of_scope_intents"]
