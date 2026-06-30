"""Tests for ESC vault MD5 dedup and shared blob index."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_vault_test"


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
    return cal, vault_mod


def test_same_md5_two_escalations_share_blob(vault_env):
    cal, vault = vault_env
    cal.enqueue_session(quickcep_session_id="qs-a", message_id="m1", env="LIVE")
    cal.enqueue_session(quickcep_session_id="qs-b", message_id="m2", env="LIVE")
    e1 = cal.open_escalation(quickcep_session_id="qs-a", reason="r1", env="LIVE")
    e2 = cal.open_escalation(quickcep_session_id="qs-b", reason="r2", env="LIVE")
    data = b"%PDF-1.4 test content same"
    md5 = hashlib.md5(data).hexdigest()

    r1 = vault.store_upload(escalation_id=e1, file_bytes=data, original_name="spec.pdf")
    r2 = vault.store_upload(escalation_id=e2, file_bytes=data, original_name="spec-copy.pdf")

    assert r1["ok"] and r2["ok"]
    assert r1["blob_md5"] == md5 == r2["blob_md5"]
    assert r2.get("deduped") is True

    blob = cal.get_vault_blob(md5)
    assert blob is not None
    assert blob["ref_count"] == 2
    assert len(cal.list_vault_links_for_escalation(escalation_id=e1)) == 1
    assert len(cal.list_vault_links_for_escalation(escalation_id=e2)) == 1


def test_cdn_url_reuse_on_blob(vault_env):
    cal, vault = vault_env
    cal.enqueue_session(quickcep_session_id="qs-c", message_id="m1", env="LIVE")
    eid = cal.open_escalation(quickcep_session_id="qs-c", reason="r", env="LIVE")
    data = b"image bytes"
    md5 = hashlib.md5(data).hexdigest()
    vault.store_upload(escalation_id=eid, file_bytes=data, original_name="photo.jpg")
    cal.set_vault_blob_cdn_url(md5=md5, cdn_url="https://quick-cep-cdn.quickcep.com/cached.jpg")
    blob = cal.get_vault_blob(md5)
    assert blob["cdn_url"] == "https://quick-cep-cdn.quickcep.com/cached.jpg"


def test_upload_token_roundtrip(vault_env):
    _, vault = vault_env
    token = vault.issue_upload_token(escalation_id=42)
    assert vault.verify_upload_token(escalation_id=42, token=token)
    assert not vault.verify_upload_token(escalation_id=43, token=token)
