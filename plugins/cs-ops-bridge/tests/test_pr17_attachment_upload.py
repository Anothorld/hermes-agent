"""Tests for PR1.7: attachment upload endpoint (multipart -> QuickCEP CDN)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_pr17_test"


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
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal17.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-att", customer_email="a@b.com", message_id="m1")
    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def _headers():
    return {"X-Bridge-Key": "test-key"}


def test_upload_attachment_returns_cdn_object(monkeypatch, app):
    cdn = _load_pkg_module("quickcep_cdn")
    monkeypatch.setattr(
        cdn, "upload_file_to_cdn",
        lambda path, feature="email": {
            "ok": True, "fileName": "spec.pdf", "fileSize": 1234,
            "url": "https://cdn.povison.com/x/spec.pdf",
        },
    )
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-att/attachments/upload",
        headers=_headers(),
        files={"file": ("spec.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"feature": "email", "operator_id": "op-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["fileName"] == "spec.pdf"
    assert body["url"].startswith("https://cdn.povison.com/")
    # Audit event recorded.
    cal = _load_pkg_module("cal")
    with cal._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM cs_conversation_events "
            "WHERE event_type='attachment_uploaded' ORDER BY id DESC LIMIT 1",
        ).fetchone()
    assert row is not None


def test_upload_attachment_unknown_session_404(app):
    client = TestClient(app)
    r = client.post(
        "/sessions/nope/attachments/upload",
        headers=_headers(),
        files={"file": ("a.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 404


def test_upload_attachment_empty_file_400(app):
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-att/attachments/upload",
        headers=_headers(),
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_attachment_cdn_failure_502(monkeypatch, app):
    cdn = _load_pkg_module("quickcep_cdn")
    monkeypatch.setattr(cdn, "upload_file_to_cdn", lambda path, feature="email": {"ok": False, "error": "cdn down"})
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-att/attachments/upload",
        headers=_headers(),
        files={"file": ("a.txt", b"data", "text/plain")},
    )
    assert r.status_code == 502


def test_upload_requires_bridge_key(app):
    client = TestClient(app)
    r = client.post(
        "/sessions/qc-att/attachments/upload",
        files={"file": ("a.txt", b"data", "text/plain")},
    )
    assert r.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
