#!/usr/bin/env python3
"""Runtime verification for operator-send + ESC close changes."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_verify_pkg"


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


def main() -> int:
    os.environ.setdefault("CS_OPS_OPERATOR_RECONCILE_CLOSE_ESC", "true")
    tmp = tempfile.mkdtemp(prefix="cs_verify_")
    os.environ["HERMES_CS_OPS_CAL_DB"] = str(Path(tmp) / "cal.db")

    detect = _load("operator_outbound_detect")
    cal = _load("cal")
    close_mod = _load("operator_escalation_close")
    sh = _load("session_handoff")
    completion = _load("escalation_completion")
    gw_mod = _load("gateway_client")

    # H1: visitor latest -> no pick
    assert (
        detect.pick_latest_operator_outbound_email(
            [
                {"ownerType": "visitor", "contentType": "html", "id": "v1"},
                {"ownerType": "operator", "contentType": "html", "id": "op1"},
            ]
        )
        is None
    )
    assert (
        detect.pick_latest_operator_outbound_email(
            [{"ownerType": "operator", "contentType": "html", "id": "op2"}]
        )["id"]
        == "op2"
    )

    # H2
    r = cal.enqueue_session(quickcep_session_id="v-s2", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r["session"]["id"], status="awaiting_expert")
    eid2 = cal.open_escalation(quickcep_session_id="v-s2", reason="test", env="LIVE")
    with patch.object(sh, "apply_handoff", return_value={"ok": True}):
        sh.handle_operator_send(
            {"chatSubSessionId": "v-s2", "id": "op-msg", "channel": "email"},
            env="LIVE",
        )
    assert cal.get_escalation(escalation_id=eid2)["state"] == "resolved"
    assert cal.get_session(quickcep_session_id="v-s2", env="LIVE")["status"] != "processing"

    # H3
    r3 = cal.enqueue_session(quickcep_session_id="v-s3", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r3["session"]["id"], status="operator_replied")
    eid3 = cal.open_escalation(quickcep_session_id="v-s3", reason="orphan", env="LIVE")
    cal.write_event(
        quickcep_session_id="v-s3",
        event_type="operator_sent",
        payload={"message_id": "op-x"},
        env="LIVE",
    )
    stats = close_mod.repair_orphaned_escalations_once(env="LIVE")
    assert stats["repaired"] == 1
    assert cal.get_escalation(escalation_id=eid3)["state"] == "resolved"

    # H4
    r4 = cal.enqueue_session(quickcep_session_id="v-s4", message_id="m1", env="LIVE")
    cal.update_session_status(session_row_id=r4["session"]["id"], status="operator_replied")
    eid4 = cal.open_escalation(quickcep_session_id="v-s4", reason="dedup", env="LIVE")
    cal.write_facts(
        quickcep_session_id="v-s4",
        namespaces={"handoff": {"last_operator_outbound_id": "same-id"}},
        env="LIVE",
    )
    out4 = sh.handle_operator_send(
        {"chatSubSessionId": "v-s4", "id": "same-id", "channel": "email"},
        env="LIVE",
    )
    assert out4.get("skipped") is True
    assert cal.get_escalation(escalation_id=eid4)["state"] == "resolved"

    # H5
    cal.enqueue_session(quickcep_session_id="v-s5", message_id="m1", env="LIVE")
    eid5 = cal.open_escalation(quickcep_session_id="v-s5", reason="resuming", env="LIVE")
    cal.claim_escalation_reply(
        escalation_id=eid5,
        operator_answer="expert",
        decided_by="expert",
        feishu_reply_message_id="om1",
    )
    cal.record_escalation_resume_run(escalation_id=eid5, run_id="run-verify-1")
    gw = MagicMock()
    gw.stop_run.return_value = True
    gw_cls = MagicMock()
    gw_cls.from_env.return_value = gw
    with patch.object(gw_mod, "GatewayClient", gw_cls):
        with patch.object(
            completion.feishu_notify,
            "notify_escalation_completed",
            return_value=MagicMock(ok=True, message_id="done-1"),
        ):
            out5 = completion.complete_resuming_escalation_superseded_by_operator(
                escalation_id=eid5,
                quickcep_session_id="v-s5",
            )
    assert out5["ok"] is True
    gw.stop_run.assert_called_once_with("run-verify-1")
    assert cal.get_escalation(escalation_id=eid5)["decision"] == "operator_manual_reply"

    print("OK: all 5 verification scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
