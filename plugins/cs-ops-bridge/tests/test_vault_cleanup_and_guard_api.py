"""Tests for vault cleanup and attachment guard HTTP context."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_vault_cleanup_test"


def _load(sub: str):
    if _PKG not in sys.modules:
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


@pytest.fixture
def vault_env(monkeypatch, tmp_path):
    db = tmp_path / "cal.db"
    vault = tmp_path / "vault"
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(db))
    monkeypatch.setenv("CS_OPS_ESC_VAULT_DIR", str(vault))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-bridge-key")
    cal = _load("cal")
    vault_mod = _load("escalation_attachment_vault")
    return cal, vault_mod, db


def test_stale_vault_links_picks_resolved_escalation(vault_env):
    cal, vault, db_path = vault_env
    cal.enqueue_session(quickcep_session_id="qs-stale", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-stale", reason="test", env="LIVE")
    vault.store_upload(escalation_id=eid, file_bytes=b"pdf-bytes", original_name="a.pdf")
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="done",
        decided_by="op",
        feishu_reply_message_id="om1",
    )
    cal.finalize_escalation(escalation_id=eid)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE cs_escalations SET updated_at=? WHERE id=?",
        ("2000-01-01T00:00:00+00:00", eid),
    )
    conn.commit()
    conn.close()

    links = cal.list_stale_vault_links(escalation_resolved_before="2025-01-01T00:00:00+00:00")
    assert any(int(row["escalation_id"]) == eid for row in links)


def test_attachment_guard_context_api(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    api = _load("plugin_api")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/cs-ops-bridge")
    client = TestClient(app)

    cal.enqueue_session(quickcep_session_id="qs-guard", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-guard", reason="r", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid,
        operator_answer="use vault pdf",
        decided_by="op",
        feishu_reply_message_id="om1",
    )
    allowed = ["https://quick-cep-cdn.quickcep.com/vault.pdf"]
    cal.merge_escalation_resume_context(
        escalation_id=eid,
        patch={"allowed_attachment_urls": allowed, "operator_attachments": []},
    )

    resp = client.get("/api/plugins/cs-ops-bridge/sessions/qs-guard/attachment-guard-context?env=LIVE")
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalation_id"] == eid
    assert body["allowed_attachment_urls"] == allowed

    empty = client.get("/api/plugins/cs-ops-bridge/sessions/unknown/attachment-guard-context?env=LIVE")
    assert empty.json()["allowed_attachment_urls"] == []


def test_manual_resume_calls_prepare(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    monkeypatch.setenv("CS_OPS_ESC_VAULT_DIR", str(tmp_path / "vault"))
    cal = _load("cal")
    resume_mod = _load("escalation_resume")
    att_mod = _load("escalation_attachments")
    gw_mod = _load("gateway_client")

    cal.enqueue_session(quickcep_session_id="qs-manual", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-manual", reason="manual", env="LIVE")

    prepared: dict = {}

    def fake_prepare(**kwargs):
        prepared.update(kwargs)
        cal.merge_escalation_resume_context(
            escalation_id=eid,
            patch={
                "operator_attachments": [
                    {"fileName": "spec.pdf", "url": "https://quick-cep-cdn.quickcep.com/spec.pdf"}
                ],
                "allowed_attachment_urls": ["https://quick-cep-cdn.quickcep.com/spec.pdf"],
            },
        )
        return {"ok": True, "count": 1}

    monkeypatch.setattr(att_mod, "prepare_escalation_attachments", fake_prepare)

    class _FakeGw:
        def start_resume_run(self, **kwargs):
            assert kwargs.get("operator_attachments")
            assert kwargs.get("allowed_attachment_urls")
            return gw_mod.LaunchOutcome(run_id="run-manual")

    with patch.object(resume_mod, "GatewayClient", type("G", (), {"from_env": staticmethod(lambda: _FakeGw())})):
        out = resume_mod.resume_escalation(
            escalation_id=eid,
            operator_answer="manual resume answer",
            decided_by="console_op",
            env="LIVE",
        )

    assert out["ok"] is True
    assert prepared.get("escalation_id") == eid


def test_prepare_preserves_previous_attachments_on_empty_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "cal.db"))
    monkeypatch.setenv("HERMES_CS_OPS_BRIDGE_KEY", "test-key")
    cal = _load("cal")
    att_mod = _load("escalation_attachments")

    cal.enqueue_session(quickcep_session_id="qs-retry", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-retry", reason="r", env="LIVE")
    prev = {
        "operator_attachments": [{"fileName": "keep.pdf", "url": "https://quick-cep-cdn.quickcep.com/keep.pdf"}],
        "allowed_attachment_urls": ["https://quick-cep-cdn.quickcep.com/keep.pdf"],
        "vault_link_ids": ["link-1"],
    }
    cal.merge_escalation_resume_context(escalation_id=eid, patch=prev)

    monkeypatch.setattr(att_mod, "upload_file_to_cdn", lambda *_a, **_k: {"ok": False, "error": "down"})

    out = att_mod.prepare_escalation_attachments(escalation_id=eid)
    assert out["count"] == 1
    esc = cal.get_escalation(escalation_id=eid)
    ctx = esc.get("resume_context") or {}
    assert ctx["operator_attachments"] == prev["operator_attachments"]
    assert ctx["allowed_attachment_urls"] == prev["allowed_attachment_urls"]
