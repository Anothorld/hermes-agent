"""Tests for draft_attachment_guard — PDF vault rules."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    name = "draft_attachment_guard_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_ROOT / "draft_attachment_guard.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_no_pdf_attachments_no_op():
    guard = _load_guard()
    items = [{"fileName": "photo.jpg", "url": "https://static.povison.com/media/review.jpg"}]
    result = guard.guard_draft_attachments(json.dumps(items), allowed_attachment_urls=[])
    assert result["blocked"] is False


def test_static_povison_pdf_blocked():
    guard = _load_guard()
    items = [
        {
            "fileName": "assembly.pdf",
            "url": "https://static.povison.com/media/assembly/guide.pdf",
        }
    ]
    result = guard.guard_draft_attachments(json.dumps(items), allowed_attachment_urls=[])
    assert result["blocked"] is True
    assert result["blocked_kind"] == "pdf_product_url"


def test_vault_cdn_pdf_allowed_when_in_list():
    guard = _load_guard()
    url = "https://quick-cep-cdn.quickcep.com/files/custom-spec.pdf"
    items = [{"fileName": "custom-spec.pdf", "url": url}]
    result = guard.guard_draft_attachments(json.dumps(items), allowed_attachment_urls=[url])
    assert result["blocked"] is False


def test_cdn_pdf_not_in_allowed_list_blocked():
    guard = _load_guard()
    url = "https://quick-cep-cdn.quickcep.com/files/not-vault.pdf"
    items = [{"fileName": "not-vault.pdf", "url": url}]
    result = guard.guard_draft_attachments(json.dumps(items), allowed_attachment_urls=[])
    assert result["blocked"] is True
    assert result["blocked_kind"] == "pdf_not_vault"


def test_attachments_contain_pdf_by_filename():
    guard = _load_guard()
    assert guard.attachments_contain_pdf(json.dumps([{"fileName": "x.PDF", "url": "https://example.com/x"}]))
    assert not guard.attachments_contain_pdf(json.dumps([{"fileName": "x.jpg", "url": "https://example.com/x.jpg"}]))


def test_guard_disabled_via_env(monkeypatch):
    guard = _load_guard()
    monkeypatch.setenv("CS_OPS_ATTACHMENT_GUARD", "0")
    items = [{"fileName": "bad.pdf", "url": "https://static.povison.com/a.pdf"}]
    result = guard.guard_draft_attachments(json.dumps(items), allowed_attachment_urls=[])
    assert result["blocked"] is False
