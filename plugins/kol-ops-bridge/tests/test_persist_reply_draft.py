"""Integration test for the toolized /reply-drafts/persist endpoint.

Calls the endpoint function directly (open-mode, no bridge key) against a
temp CAL DB and asserts the draft event + approval.reply_draft fact are
written with an enriched (to / Re:subject) envelope.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def _body(plugin_api, **over):
    base = {
        "identity_id": over.pop("identity_id"),
        "campaign_id": "C1",
        "env": "TEST",
        "source_message_id": "M1",
        "primary_lane": "commerce",
        "primary_goal": "compensation_negotiation",
        "child_skill": "kol-compensation-negotiator",
        "child_envelope": {"body": "We can do $1200."},
        "latest_email": {"from": "kol@x.com", "subject": "budget", "thread_id": "TH1"},
    }
    base.update(over)
    return plugin_api.PersistReplyDraftBody(**base)


@pytest.fixture(autouse=True)
def _open_mode(monkeypatch, cal_db):
    """Bypass bridge-key auth — these tests exercise persistence, not auth.

    Depends on ``cal_db`` so the synthetic package (and plugin_api's
    ``from . import ...`` submodules) are loaded before we patch.
    """
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _provided: None)


def test_persist_enriches_and_writes(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t1", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")

    out = plugin_api.persist_reply_draft(_body(plugin_api, identity_id=iid),
                                         x_bridge_key=None)
    assert out["ok"] is True
    assert out["draft"]["to"] == "kol@x.com"
    assert out["draft"]["subject"] == "Re: budget"
    assert out["draft"]["thread_id"] == "TH1"
    assert out["written"].get("approval") == 1

    # The approval.reply_draft fact is now persisted + readable.
    facts = cal_db.latest_facts_for(identity_id=iid, campaign_id="C1", env="TEST")
    draft_fact = facts["approval.reply_draft"]
    assert draft_fact["decision"] == "pending"
    assert draft_fact["draft"]["to"] == "kol@x.com"


def test_persist_with_contributing_skills(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t1c", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")
    contributing = [
        {"lane": "commerce", "goal": "product_selection", "skill": "kol-product-selector"},
        {"lane": "commerce", "goal": "deliverables_scope", "skill": "kol-deliverables-clarifier"},
    ]
    out = plugin_api.persist_reply_draft(
        _body(
            plugin_api,
            identity_id=iid,
            child_skill="kol-reply-synthesizer",
            primary_goal="product_selection",
            contributing=contributing,
        ),
        x_bridge_key=None,
    )
    assert out["ok"] is True
    facts = cal_db.latest_facts_for(identity_id=iid, campaign_id="C1", env="TEST")
    draft_fact = facts["approval.reply_draft"]
    assert draft_fact["contributing_skills"] == contributing
    assert draft_fact["child_skill"] == "kol-reply-synthesizer"


def test_persist_missing_recipient_400(cal_db):
    plugin_api = _load_plugin_api()
    iid = cal_db.upsert_identity(primary_handle="t2", platform="instagram")
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")
    body = _body(plugin_api, identity_id=iid,
                 latest_email={"subject": "x"})  # no from/from_addr
    with pytest.raises(HTTPException) as exc:
        plugin_api.persist_reply_draft(body, x_bridge_key=None)
    assert exc.value.status_code == 400


def test_persist_unknown_identity_404(cal_db):
    plugin_api = _load_plugin_api()
    cal_db.upsert_campaign_config(campaign_id="C1", env="TEST")
    body = _body(plugin_api, identity_id=999999)
    with pytest.raises(HTTPException) as exc:
        plugin_api.persist_reply_draft(body, x_bridge_key=None)
    assert exc.value.status_code == 404
