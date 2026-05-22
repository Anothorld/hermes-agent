"""Regression: bridge ``_approve_or_reject`` must preserve a non-dict
previous fact value when stamping the operator decision. Before the fix,
a scalar payload (e.g. the resumer writing
``approval.outreach_missing_public_email_resolution =
"use_campaign_test_mode_to_only"``) was discarded — only
``{decision: approved, decided_by: ...}`` survived, so a resumed agent
could not recover what was being approved.

Calls ``_approve_or_reject`` directly (matching the in-process pattern
used by ``test_lanes_enrichment.py``) to avoid pulling FastAPI's
TestClient into the bridge test rig.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ``plugin_api`` imports FastAPI at module load time. Bridge CI runs in a
# venv that does not always ship FastAPI; the value-preservation logic
# under test still lives in that module, so skip cleanly when it's not
# available rather than failing collection.
pytest.importorskip("fastapi")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(
        fq, _PLUGIN_ROOT / "plugin_api.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def _body(plugin_api, identity_id: int, campaign_id: str):
    return plugin_api.ApprovalDecisionBody(
        identity_id=identity_id,
        campaign_id=campaign_id,
        decided_by="console-user",
        env="TEST",
    )


def test_scalar_prior_value_preserved_after_approve(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST",
                                  test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid, campaign_id="C1", namespace="approval",
        facts={"approval.foo": "scalar_proposal"},
        source="resumer", env="TEST",
    )
    plugin_api._approve_or_reject(
        fact_path="approval.foo", decision="approved",
        body=_body(plugin_api, iid, "C1"),
    )
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="C1", env="TEST",
    ).get("approval.foo")
    assert isinstance(latest, dict)
    assert latest["decision"] == "approved"
    assert latest["value"] == "scalar_proposal"


def test_dict_prior_value_preserved_after_approve(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t2", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C2", env="TEST",
                                  test_mode_to="t@x.com")
    cal_db.write_facts(
        identity_id=iid, campaign_id="C2", namespace="approval",
        facts={"approval.bar": {"amount": 1500, "currency": "USD"}},
        source="resumer", env="TEST",
    )
    plugin_api._approve_or_reject(
        fact_path="approval.bar", decision="approved",
        body=_body(plugin_api, iid, "C2"),
    )
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="C2", env="TEST",
    ).get("approval.bar")
    assert latest["decision"] == "approved"
    assert latest["amount"] == 1500
    assert latest["currency"] == "USD"


def test_no_prior_value_still_writes_decision(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t3", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C3", env="TEST",
                                  test_mode_to="t@x.com")
    plugin_api._approve_or_reject(
        fact_path="approval.baz", decision="approved",
        body=_body(plugin_api, iid, "C3"),
    )
    latest = cal_db.latest_facts_for(
        identity_id=iid, campaign_id="C3", env="TEST",
    ).get("approval.baz")
    assert latest["decision"] == "approved"
    assert "value" not in latest


def test_approved_linked_reply_draft_resolves_escalation(cal_db, monkeypatch):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t4", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C4", env="TEST",
                                  test_mode_to="t@x.com")
    escalation_id = cal_db.open_escalation(
        identity_id=iid,
        campaign_id="C4",
        env="TEST",
        goal="compensation_negotiation",
        reason="paid_quote_over_ceiling",
        question_to_operator="Approve cap or provide counter guidance?",
    )
    cal_db.write_facts(
        identity_id=iid,
        campaign_id="C4",
        namespace="approval",
        facts={"approval.reply_draft": {
            "decision": "pending",
            "linked_escalation_id": escalation_id,
            "draft": {"to": "t@x.com", "subject": "s", "body": "b"},
        }},
        source="resumer",
        env="TEST",
    )
    monkeypatch.setattr(
        plugin_api,
        "_create_gmail_draft_for_reply_approval",
        lambda **_: {"draft_id": "d1", "thread_id": "t1"},
    )

    out = plugin_api._approve_or_reject(
        fact_path="approval.reply_draft",
        decision="approved",
        body=_body(plugin_api, iid, "C4"),
    )

    rows = {r["id"]: r for r in cal_db.list_escalations(env="TEST")}
    assert out["linked_escalation_id"] == escalation_id
    assert out["handled_escalation_id"] == escalation_id
    assert rows[escalation_id]["state"] == "resolved"
    assert rows[escalation_id]["operator_answer"].startswith("Linked approval.reply_draft was approved")
