"""Tests for PR3: edit_memory guard whitelist + start_edit_memory_run + baseline snapshot."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_GUARD_ROOT = Path(__file__).resolve().parents[1]
_BRIDGE_ROOT = _GUARD_ROOT.parent / "cs-ops-bridge"


def _load_guard():
    spec = importlib.util.spec_from_file_location("pr3_send_guard", _GUARD_ROOT / "send_guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Guard whitelist ────────────────────────────────────────────────────


def test_edit_memory_allows_knowledge_tools():
    sg = _load_guard()
    for tool in ("knowledge_retain", "knowledge_recall"):
        assert sg.pre_tool_block(tool_name=tool, args={}, run_kind="edit_memory") is None


def test_edit_memory_blocks_legacy_hindsight_tools():
    """Legacy hindsight_* tools are blocked for edit_memory (replaced by the dedicated knowledge toolset)."""
    sg = _load_guard()
    for tool in ("hindsight_retain", "hindsight_recall", "hindsight_reflect"):
        out = sg.pre_tool_block(tool_name=tool, args={}, run_kind="edit_memory")
        assert out is not None
        assert out["action"] == "block"


def test_edit_memory_blocks_non_hindsight_tools():
    sg = _load_guard()
    for tool in ("terminal", "execute_code", "send_message", "delegate_task", "cs_bridge_tool"):
        out = sg.pre_tool_block(tool_name=tool, args={}, run_kind="edit_memory")
        assert out is not None
        assert out["action"] == "block"
        assert "knowledge" in out["message"]


def test_edit_memory_whitelist_ignores_session_scope():
    """edit_memory whitelist applies regardless of session_id (not tied to povison session)."""
    sg = _load_guard()
    out = sg.pre_tool_block(
        tool_name="terminal", args={"command": "rm -rf /"},
        run_kind="edit_memory", session_id="anything",
    )
    assert out is not None
    assert out["action"] == "block"


def test_normal_run_unaffected_by_run_kind_default():
    sg = _load_guard()
    # run_kind default "" → normal guard path; clean terminal command passes.
    out = sg.pre_tool_block(
        tool_name="terminal", args={"command": "ls -la"},
        task_id="povison-cs:LIVE:123",
    )
    assert out is None


# ── start_edit_memory_run passes run_kind ──────────────────────────────


_PKG = "cs_ops_bridge_pr3_test"


def _load_bridge_module(sub: str):
    if _PKG not in sys.modules:
        import types

        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_BRIDGE_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full, _BRIDGE_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_BRIDGE_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


def test_start_edit_memory_run_includes_run_kind(monkeypatch):
    cal = _load_bridge_module("cal")  # noqa: F841 — ensure package loads
    gw = _load_bridge_module("gateway_client")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(gw, "try_acquire_launch", lambda _k: True)
    monkeypatch.setattr(gw, "release_launch", lambda _k: None)
    monkeypatch.setattr(gw, "launch_dedup_key", lambda sid, mid: f"{sid}:{mid}")
    monkeypatch.setattr(gw, "post_run_with_retry", lambda *, base, api_key, body: captured.__setitem__("body", body) or {"run_id": "run-em-1"})
    monkeypatch.setattr(gw, "drain_run_events", lambda *, base, api_key, run_id: None)
    monkeypatch.setenv("CS_OPS_GATEWAY_YOLO", "1")

    client = gw.GatewayClient(base="http://gw", api_key="k")
    outcome = client.start_edit_memory_run(
        quickcep_session_id="qc-em",
        env="LIVE",
        ai_draft_html="<p>AI says 200kg</p>",
        operator_draft_html="<p>Actual 150kg</p>",
        operator_id="op-3",
    )
    assert outcome.run_id == "run-em-1"
    assert captured["body"]["run_kind"] == "edit_memory"
    assert "operator-edited draft" in captured["body"]["input"]
    assert "150kg" in captured["body"]["input"]


# ── save_draft AI baseline snapshot ────────────────────────────────────


@pytest.fixture()
def cal_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CS_OPS_CAL_DB", str(tmp_path / "pr3.db"))
    for name in list(sys.modules):
        if name.startswith(_PKG):
            del sys.modules[name]
    return _load_bridge_module("cal")


def test_operator_edit_snapshots_ai_baseline(cal_db):
    cal = cal_db
    cal.enqueue_session(quickcep_session_id="qc-em2", customer_email="a@b.com", message_id="m1")
    cal.save_draft(quickcep_session_id="qc-em2", draft_html="<p>AI: 200kg</p>", source="agent")
    # Operator edits → baseline snapshot written.
    cal.save_draft(quickcep_session_id="qc-em2", draft_html="<p>Actual: 150kg</p>", source="operator_edit")
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-em2") or {}
    facts = (ctx.get("facts") or {})
    assert facts["edit_memory"]["ai_baseline_html"] == "<p>AI: 200kg</p>"


def test_agent_draft_no_baseline_snapshot(cal_db):
    cal = cal_db
    cal.enqueue_session(quickcep_session_id="qc-em3", customer_email="a@b.com", message_id="m1")
    cal.save_draft(quickcep_session_id="qc-em3", draft_html="<p>AI</p>", source="agent")
    ctx = cal.get_dispatch_context(quickcep_session_id="qc-em3") or {}
    facts = (ctx.get("facts") or {})
    assert "edit_memory" not in facts


# ── send_reply triggers edit_memory run ────────────────────────────────


def test_send_reply_triggers_edit_memory_for_operator_edit(cal_db, monkeypatch, tmp_path):
    cal = cal_db
    cal.enqueue_session(quickcep_session_id="qc-em4", customer_email="a@b.com", message_id="m1")
    sess = cal.get_session(quickcep_session_id="qc-em4")
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    cal.update_session_chat_id(session_row_id=sess["id"], chat_session_id="chat-em")
    cal.save_draft(quickcep_session_id="qc-em4", draft_html="<p>AI: 200kg</p>", source="agent")
    cal.save_draft(quickcep_session_id="qc-em4", draft_html="<p>Actual: 150kg</p>", source="operator_edit")

    send_mod = _load_bridge_module("send_reply")
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(send_mod, "_quickcep_cli_path", lambda: cli)
    monkeypatch.setattr(send_mod, "_guard_draft", lambda content, att: None)
    monkeypatch.setattr(
        send_mod.subprocess, "run",
        lambda argv, **kwargs: MagicMock(returncode=0, stdout='{"success":true}', stderr=""),
    )
    monkeypatch.setattr(send_mod, "fetch_messages", lambda *, quickcep_session_id: {"messages": [{"id": "out-em"}]})
    monkeypatch.setattr(send_mod, "handle_operator_send", lambda info, env=None: {"ok": True})

    gw = _load_bridge_module("gateway_client")
    em_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(gw.GatewayClient, "start_edit_memory_run",
                        lambda self, *, quickcep_session_id, env, ai_draft_html, operator_draft_html, operator_id="":
                        em_calls.append({"qsid": quickcep_session_id, "ai": ai_draft_html, "op": operator_draft_html})
                        or MagicMock(run_id="run-em-4", dedup_skipped=False))

    res = send_mod.send_reply(quickcep_session_id="qc-em4", operator_id="op-3", operator_name="Bob")
    assert res["ok"] is True
    assert res["edit_memory"]["run_id"] == "run-em-4"
    assert em_calls[0]["ai"] == "<p>AI: 200kg</p>"
    assert em_calls[0]["op"] == "<p>Actual: 150kg</p>"


def test_send_reply_no_edit_memory_for_agent_draft(cal_db, monkeypatch, tmp_path):
    """Pure agent draft (no operator edit) → no edit_memory launch."""
    cal = cal_db
    cal.enqueue_session(quickcep_session_id="qc-em5", customer_email="a@b.com", message_id="m1")
    sess = cal.get_session(quickcep_session_id="qc-em5")
    cal.update_session_status(session_row_id=sess["id"], status="draft_ready")
    cal.update_session_chat_id(session_row_id=sess["id"], chat_session_id="chat-em5")
    cal.save_draft(quickcep_session_id="qc-em5", draft_html="<p>AI only</p>", source="agent")

    send_mod = _load_bridge_module("send_reply")
    cli = tmp_path / "quickcep_cli.py"
    cli.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(send_mod, "_quickcep_cli_path", lambda: cli)
    monkeypatch.setattr(send_mod, "_guard_draft", lambda content, att: None)
    monkeypatch.setattr(
        send_mod.subprocess, "run",
        lambda argv, **kwargs: MagicMock(returncode=0, stdout='{"success":true}', stderr=""),
    )
    monkeypatch.setattr(send_mod, "fetch_messages", lambda *, quickcep_session_id: {"messages": [{"id": "out"}]})
    monkeypatch.setattr(send_mod, "handle_operator_send", lambda info, env=None: {"ok": True})
    gw = _load_bridge_module("gateway_client")
    monkeypatch.setattr(gw.GatewayClient, "start_edit_memory_run",
                        lambda self, **kw: pytest.fail("edit_memory should not launch for agent draft"))

    res = send_mod.send_reply(quickcep_session_id="qc-em5")
    assert res["ok"] is True
    assert "edit_memory" not in res


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
