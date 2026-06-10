"""Tests for inbound reply orchestrator resilience."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from kol_ops_bridge_pkg.inbound_reply.schemas import ProcessStatus


@dataclass
class _StubMsg:
    message_id: str
    from_addr: str = "kol@example.com"


@dataclass
class _FakeMailbox:
    user_id: int
    google_email: str
    client: Any


def test_orchestrator_saves_seen_after_success_before_later_failure(
    bridge_pkg,
    tmp_path,
    monkeypatch,
):
    from kol_ops_bridge_pkg.inbound_reply import orchestrator as orchestrator_mod
    from kol_ops_bridge_pkg.inbound_reply import state as state_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    state_mod._STATE_PATH = tmp_path / "hermes" / "kol-ops-bridge" / "poller_state.json"
    state_mod._LOCK_PATH = state_mod._STATE_PATH.with_suffix(".lock")

    calls: list[str] = []

    def _process(msg, env, *, client, deps, mailbox_user_id=0, mailbox_email=""):
        calls.append(msg.message_id)
        if msg.message_id == "msg-b":
            raise RuntimeError("simulated crash")
        return "dispatched" if msg.message_id == "msg-a" else "skipped"

    monkeypatch.setattr(orchestrator_mod, "process_message", _process)
    monkeypatch.setattr(
        orchestrator_mod,
        "list_operator_gmail_clients",
        lambda: [
            _FakeMailbox(
                user_id=1,
                google_email="ops@brand.com",
                client=MagicMock(
                    search=MagicMock(
                        return_value=[
                            _StubMsg("msg-a"),
                            _StubMsg("msg-b"),
                        ],
                    ),
                    get_message=MagicMock(
                        side_effect=lambda mid: MagicMock(
                            message_id=mid,
                            from_addr="kol@example.com",
                            thread_id="t1",
                            subject="Re: hi",
                            body="hello",
                            to="ops@brand.com",
                            cc="",
                            snippet="hello",
                            date="Mon, 1 Jun 2026 10:00:00 +0000",
                            in_reply_to="",
                            references="",
                        ),
                    ),
                ),
            )
        ],
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "global_message_seen",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "record_global_message_seen",
        lambda **kwargs: None,
    )

    stats = orchestrator_mod.run_once(env="TEST", lookback_days=3, max_results=10, deps=MagicMock())
    assert stats["matched"] == 1
    assert stats["errors"] == 1
    assert stats["retry"] == 1
    saved = state_mod.load_state()
    assert "msg-a" in saved["seen_TEST_1"]
    assert "msg-b" not in saved["seen_TEST_1"]


def test_orchestrator_reprocesses_locally_seen_when_gateway_retry_due(
    bridge_pkg,
    tmp_path,
    monkeypatch,
):
    from kol_ops_bridge_pkg.inbound_reply import orchestrator as orchestrator_mod
    from kol_ops_bridge_pkg.inbound_reply import state as state_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    state_mod._STATE_PATH = tmp_path / "hermes" / "kol-ops-bridge" / "poller_state.json"
    state_mod._LOCK_PATH = state_mod._STATE_PATH.with_suffix(".lock")
    state_mod.save_state({"seen_LIVE_1": ["msg-recover"]})

    calls: list[str] = []

    def _process(msg, env, *, client, deps, mailbox_user_id=0, mailbox_email=""):
        calls.append(msg.message_id)
        return "dispatched"

    monkeypatch.setattr(orchestrator_mod, "process_message", _process)
    monkeypatch.setattr(
        orchestrator_mod,
        "needs_reprocess_after_global_seen",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "list_operator_gmail_clients",
        lambda: [
            _FakeMailbox(
                user_id=1,
                google_email="ops@brand.com",
                client=MagicMock(
                    search=MagicMock(return_value=[_StubMsg("msg-recover")]),
                    get_message=MagicMock(
                        return_value=MagicMock(
                            message_id="msg-recover",
                            from_addr="kol@example.com",
                        ),
                    ),
                ),
            )
        ],
    )
    monkeypatch.setattr(orchestrator_mod, "global_message_seen", lambda **kwargs: False)
    monkeypatch.setattr(orchestrator_mod, "record_global_message_seen", lambda **kwargs: None)

    stats = orchestrator_mod.run_once(env="LIVE", lookback_days=3, max_results=10, deps=MagicMock())
    assert calls == ["msg-recover"]
    assert stats["matched"] == 1
