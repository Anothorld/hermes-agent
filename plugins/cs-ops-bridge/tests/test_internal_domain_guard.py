"""Tests for the internal domain guard in draft-save flow."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN_ROOT / "scripts"


def _load_cs_bridge_tool():
    name = "cs_bridge_tool_guard_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "cs_bridge_tool.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_guard():
    name = "internal_domain_guard_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_ROOT / "internal_domain_guard.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Guard module unit tests ──────────────────────────────────────────


def test_guard_clean_content_passes():
    guard = _load_guard()
    result = guard.check_content("<p>Hello customer, please visit povison.com</p>")
    assert result["blocked"] is False
    assert result["matches"] == []


def test_guard_blocks_musem_scm_oss_url():
    guard = _load_guard()
    content = '<p>Here is your photo: <img src="http://musem-scm-public.oss-cn-guangzhou.aliyuncs.com/srm/qc/test.jpg"></p>'
    result = guard.check_content(content)
    assert result["blocked"] is True
    assert len(result["matches"]) > 0
    assert any("musem-scm" in m for m in result["matches"])


def test_guard_blocks_obfuscated_oss_url():
    """The specific case from danielleamaral81@hotmail.com — = encoded internal URL."""
    guard = _load_guard()
    content = '<p>See: http://musem-scm=public.oss-cn=guangzhou.alyuncs.com/image.jpg</p>'
    result = guard.check_content(content)
    assert result["blocked"] is True


def test_guard_blocks_localhost():
    guard = _load_guard()
    result = guard.check_content('<p>API at http://localhost:8081/health</p>')
    assert result["blocked"] is True
    assert any("localhost" in m for m in result["matches"])


def test_guard_blocks_internal_port_in_url():
    guard = _load_guard()
    result = guard.check_content('<p>Check http://192.168.1.100:8643/api/status</p>')
    assert result["blocked"] is True
    # Should match both 192.168. and :8643/
    assert len(result["matches"]) >= 1


def test_guard_blocks_feishu_link():
    guard = _load_guard()
    result = guard.check_content('<p>Discussion: https://open.feishu.cn/chat/abc123</p>')
    assert result["blocked"] is True


def test_guard_allows_povison_com():
    guard = _load_guard()
    result = guard.check_content('<p>Visit https://www.povison.com/sofas.html</p>')
    assert result["blocked"] is False


def test_guard_allows_static_povison_cdn():
    """static.povison.com is the customer-facing CDN — must NOT be blocked."""
    guard = _load_guard()
    result = guard.check_content('<img src="https://static.povison.com/media/catalog/product/sofa.jpg">')
    assert result["blocked"] is False


def test_guard_attachments_clean():
    guard = _load_guard()
    atts = json.dumps([
        {"name": "Front.jpg", "url": "https://static.povison.com/media/qc/front.jpg"},
        {"name": "Side.jpg", "url": "https://static.povison.com/media/qc/side.jpg"},
    ])
    result = guard.check_attachments(atts)
    assert result["blocked"] is False


def test_guard_attachments_blocked():
    guard = _load_guard()
    atts = json.dumps([
        {"name": "Photo.jpg", "url": "http://musem-scm-public.oss-cn-guangzhou.aliyuncs.com/qc/photo.jpg"},
    ])
    result = guard.check_attachments(atts)
    assert result["blocked"] is True


def test_guard_attachments_none():
    guard = _load_guard()
    result = guard.check_attachments(None)
    assert result["blocked"] is False


def test_guard_combined_content_and_attachments():
    guard = _load_guard()
    content = "<p>Good content</p>"
    atts = json.dumps([{"name": "X.jpg", "url": "http://localhost:8081/img.jpg"}])
    result = guard.guard_draft(content, atts)
    assert result["blocked"] is True
    assert "attachments" in result["source"]


def test_guard_combined_both_blocked():
    guard = _load_guard()
    content = '<p>Link: http://musem-scm-public.oss-cn-guangzhou.aliyuncs.com/a.jpg</p>'
    atts = json.dumps([{"name": "X.jpg", "url": "http://localhost:8081/img.jpg"}])
    result = guard.guard_draft(content, atts)
    assert result["blocked"] is True
    assert "content" in result["source"]
    assert "attachments" in result["source"]
    assert len(result["matches"]) >= 2


def test_guard_error_message_format():
    guard = _load_guard()
    result = guard.guard_draft('<p>http://localhost:8081/</p>', None)
    assert result["blocked"] is True
    assert "Internal domain guard" in result["error"]
    assert "Remove all internal/backend links" in result["error"]


def test_guard_empty_content():
    guard = _load_guard()
    result = guard.check_content("")
    assert result["blocked"] is False


# ── Integration test: cs_bridge_tool _cmd_draft_save with guard ─────


def test_draft_save_aborts_on_internal_domain(tmp_path, monkeypatch):
    """draft-save must abort when content contains an internal domain."""
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    # Track if join-chat was called — it should NOT be called when guard blocks
    calls: list[list[str]] = []

    def fake_run(_cli, argv, timeout=120):
        calls.append(list(argv))
        return MagicMock(returncode=0, stdout='{"result_code":200}', stderr="")

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)

    args = MagicMock(
        session_id="sess-guard",
        content='<p>Photo: http://musem-scm-public.oss-cn-guangzhou.aliyuncs.com/test.jpg</p>',
        content_file=None,
        subject="Re: test",
        receiver="a@b.com",
        attachments=None,
    )
    with patch.object(tool, "print_json") as mock_print, patch.object(
        tool.sys, "exit", side_effect=SystemExit(2)
    ):
        with pytest.raises(SystemExit):
            tool._cmd_draft_save(args)

    out = mock_print.call_args[0][0]
    assert "Internal domain guard" in out["error"]
    assert out["session_id"] == "sess-guard"
    # join-chat must NOT have been called
    assert all(c[0] != "join-chat" for c in calls)


def test_draft_save_proceeds_on_clean_content(tmp_path, monkeypatch):
    """draft-save must proceed normally when content has no internal domains."""
    tool = _load_cs_bridge_tool()
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(tool, "_quickcep_cli_path", lambda: cli)

    def fake_run(_cli, argv, timeout=120):
        if argv[0] == "join-chat":
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"action": "join_chat", "result_code": 200}),
                stderr="",
            )
        return MagicMock(
            returncode=0,
            stdout=json.dumps({"action": "draft_save", "success": True, "result_code": 200}),
            stderr="",
        )

    monkeypatch.setattr(tool, "_run_quickcep_cli", fake_run)

    args = MagicMock(
        session_id="sess-clean",
        content="<p>Hello! Visit https://www.povison.com for more info.</p>",
        content_file=None,
        subject="Re: test",
        receiver="a@b.com",
        attachments=None,
    )
    with patch.object(tool, "print_json") as mock_print:
        tool._cmd_draft_save(args)

    out = mock_print.call_args[0][0]
    assert out["success"] is True
