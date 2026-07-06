"""Correction API tests — operator改标 writes cs_intent_corrections, no auto-relaunch."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

# Use a temp DB
@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_INTENT_DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture
def client():
    from cs_intent_classifier_pkg import plugin_api  # type: ignore[attr-defined]
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def test_classify_then_correct(client):
    # 1. classify
    r = client.post("/classify", json={
        "session_id": "s1",
        "env": "TEST",
        "subject": "Where is my order?",
        "body": "Where is my order #11223344?",
        "metadata": {},
    })
    assert r.status_code == 200
    ge = r.json()["gate_extract"]
    assert ge["primary_intent"] == "logistics_inquiry"

    # 2. correct: operator thinks it's actually after_sale
    r2 = client.patch("/intent/s1", json={
        "env": "TEST",
        "operator_id": "op1",
        "primary_intent": "after_sale_issue",
        "intent_overrides": [{"intent": "after_sale_issue", "in_scope": True, "reason": "实为售后"}],
        "reason": "误分为物流，实为售后",
    })
    assert r2.status_code == 200
    out = r2.json()
    assert out["corrected"]["primary_intent"] == "after_sale_issue"
    assert out["corrections"]
    assert out["corrections"][0]["operator_id"] == "op1"


def test_correct_missing_prediction_404(client):
    r = client.patch("/intent/nope", json={
        "env": "TEST",
        "operator_id": "op1",
        "primary_intent": "product_inquiry",
        "reason": "x",
    })
    assert r.status_code == 404


def test_get_intent_returns_predicted_only_when_no_correction(client):
    client.post("/classify", json={
        "session_id": "s2",
        "env": "TEST",
        "subject": "Sofa dimensions",
        "body": "What are the dimensions of SF8268?",
        "metadata": {},
    })
    r = client.get("/intent/s2", params={"env": "TEST"})
    assert r.status_code == 200
    out = r.json()
    assert out["predicted"] is not None
    assert out["corrected"] is None


def test_gate_extract_404_when_unclassified(client):
    r = client.get("/gate-extract/never", params={"env": "TEST"})
    assert r.status_code == 404


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
