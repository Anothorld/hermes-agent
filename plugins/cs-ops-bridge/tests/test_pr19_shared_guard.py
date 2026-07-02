"""Tests for PR1.9: shared draft_guard.gaurd_draft_content + PUT /draft guard."""

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
_PKG = "cs_ops_bridge_pr19_test"


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


def test_guard_draft_content_allows_clean():
    g = _load_pkg_module("draft_guard")
    assert g.guard_draft_content("<p>正常回复</p>", []) is None
    assert g.guard_draft_content("<p>ok</p>", None) is None


def test_guard_draft_content_blocks_internal_domain():
    g = _load_pkg_module("draft_guard")
    # internal_domain_guard blocks localhost / internal IPs.
    block = g.guard_draft_content('<p>API at http://localhost:8081/health</p>', [])
    assert block is not None
    assert block["blocked"] is True
    assert block["source"] in ("content", "attachments")
    assert block["error"]


def test_guard_accepts_list_or_json_string_attachments():
    g = _load_pkg_module("draft_guard")
    # Both forms should be accepted without raising.
    assert g.guard_draft_content("<p>ok</p>", [{"fileName": "a.txt", "url": "https://x/a.txt"}]) is None
    assert g.guard_draft_content("<p>ok</p>", '[{"fileName":"a.txt","url":"https://x/a.txt"}]') is None


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal19.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    cal = _load_pkg_module("cal")
    cal.enqueue_session(quickcep_session_id="qc-guard", customer_email="a@b.com", message_id="m1")
    plugin_api = _load_pkg_module("plugin_api")
    app = FastAPI()
    app.include_router(plugin_api.router)
    return app


def _headers():
    return {"X-Bridge-Key": "test-key"}


def test_put_draft_runs_guard_server_side(app):
    """PR1.9: PUT /draft enforces the shared guard for Console-originated drafts."""
    client = TestClient(app)
    r = client.put(
        "/sessions/qc-guard/draft",
        headers=_headers(),
        json={"env": "LIVE", "draft_html": '<p>API at http://localhost:8081/health</p>', "attachments": []},
    )
    assert r.status_code == 422
    # Draft must not have been persisted.
    cal = _load_pkg_module("cal")
    sess = cal.get_session(quickcep_session_id="qc-guard")
    assert sess["draft_html"] is None


def test_put_draft_accepts_clean(app):
    client = TestClient(app)
    r = client.put(
        "/sessions/qc-guard/draft",
        headers=_headers(),
        json={"env": "LIVE", "draft_html": "<p>正常回复</p>", "attachments": [], "source": "operator_edit"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["stored"] == "cal"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
