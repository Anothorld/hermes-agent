"""CLI + contract tests for persist-initial-outreach-draft."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN_ROOT / "scripts"
_CLI = _SCRIPTS / "kol_bridge_tool.py"


def _load_contract():
    path = _PLUGIN_ROOT / "bridge_agent_contract.py"
    spec = importlib.util.spec_from_file_location("bac_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(
        fq, _PLUGIN_ROOT / "plugin_api.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cold_outreach_thread_anchor_stable():
    c = _load_contract()
    a = c.cold_outreach_thread_anchor(campaign_id="C1", identity_id=42)
    b = c.cold_outreach_thread_anchor(campaign_id="C1", identity_id=42)
    assert a == b
    assert a["source_message_id"] == "draft:outreach_C1_42"
    assert a["thread_id"] == "outreach_C1_42"


def test_persist_initial_outreach_via_api(cal_db, monkeypatch, bridge_pkg):
    _ = bridge_pkg
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _k: None)
    iid = cal_db.upsert_identity(
        primary_handle="cold1",
        platform="instagram",
        primary_email="kol@example.com",
    )
    cal_db.upsert_campaign_config(campaign_id="C-OUT", env="TEST", test_mode_to="t@x.com")

    body = plugin_api.PersistReplyDraftBody(
        identity_id=iid,
        campaign_id="C-OUT",
        env="TEST",
        source_message_id="draft:outreach_C-OUT_{}".format(iid),
        primary_lane="commerce",
        primary_goal="outreach",
        child_skill="kol-cold-outreach",
        child_envelope={
            "subject": "POVISON collab",
            "body": "<p>Hi</p>",
            "to": "kol@example.com",
        },
        latest_email={
            "thread_id": f"outreach_C-OUT_{iid}",
            "message_id": f"draft:outreach_C-OUT_{iid}",
            "subject": "POVISON collab",
        },
    )
    out = plugin_api.persist_reply_draft(body, x_bridge_key=None)
    assert out["ok"] is True
    facts = cal_db.latest_facts_for(identity_id=iid, campaign_id="C-OUT", env="TEST")
    assert facts["approval.reply_draft"]["draft"]["to"] == "kol@example.com"
    assert facts.get("offer.outreach_draft_ready") is True


def _persist_outreach_body(plugin_api, cal_db, *, iid: str, raw_body: str) -> dict:
    body = plugin_api.PersistReplyDraftBody(
        identity_id=iid,
        campaign_id="C-OUT",
        env="TEST",
        source_message_id="draft:outreach_C-OUT_{}".format(iid),
        primary_lane="commerce",
        primary_goal="outreach",
        child_skill="kol-cold-outreach",
        child_envelope={
            "subject": "POVISON collab",
            "body": raw_body,
            "to": "kol@example.com",
        },
        latest_email={
            "thread_id": f"outreach_C-OUT_{iid}",
            "message_id": f"draft:outreach_C-OUT_{iid}",
            "subject": "POVISON collab",
        },
    )
    plugin_api.persist_reply_draft(body, x_bridge_key=None)
    facts = cal_db.latest_facts_for(identity_id=iid, campaign_id="C-OUT", env="TEST")
    return facts["approval.reply_draft"]["draft"]


def test_plain_text_outreach_body_normalized_to_html(cal_db, monkeypatch, bridge_pkg):
    """A plain-text marketing paragraph must never persist as-is (POVISON 683)."""
    _ = bridge_pkg
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _k: None)
    iid = cal_db.upsert_identity(primary_handle="cold_plain", platform="instagram",
                                 primary_email="kol@example.com")
    cal_db.upsert_campaign_config(campaign_id="C-OUT", env="TEST", test_mode_to="t@x.com")

    draft = _persist_outreach_body(
        plugin_api, cal_db, iid=iid,
        raw_body=(
            "Hi McKenna,\n\nWe love your work. See https://www.povison.com/tv-stand "
            "for the piece.\n\nBest,\nPOVISON Team"
        ),
    )
    assert "<p>" in draft["body"]
    assert draft["html"] is True
    # Bare product URL must become a clickable link.
    assert '<a href="https://www.povison.com/tv-stand">' in draft["body"]


def test_html_outreach_body_preserved(cal_db, monkeypatch, bridge_pkg):
    """An already-HTML body is kept verbatim (idempotent)."""
    _ = bridge_pkg
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _k: None)
    iid = cal_db.upsert_identity(primary_handle="cold_html", platform="instagram",
                                 primary_email="kol@example.com")
    cal_db.upsert_campaign_config(campaign_id="C-OUT", env="TEST", test_mode_to="t@x.com")

    html = '<p>Hi Mary,</p><p>We just launched <a href="https://x.com/s">the sofa</a>.</p>'
    draft = _persist_outreach_body(plugin_api, cal_db, iid=iid, raw_body=html)
    assert draft["body"] == html
    assert draft["html"] is True


def test_write_event_preflight_missing_identity():
    proc = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "write-event",
            "--env",
            "LIVE",
            "--event-type",
            "shortlist_approval_received",
            "--actor",
            "owner@console.app",
            "--json",
            '{"campaign_id":"X"}',
        ],
        cwd=_PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stderr.strip().splitlines()[-1])
    assert payload["error"] == "invalid_cli_args"
    hint = payload.get("hint", "") + str(payload.get("missing", ""))
    assert "identity_id" in hint or "--identity-id" in hint


def test_get_user_style_without_owner_returns_empty(cal_db, monkeypatch, bridge_pkg):
    """user_style GET without an owner id must not 400-derail a drafting run."""
    _ = bridge_pkg
    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _k: None)
    out = plugin_api.get_policy("user_style", owner_user_id=None, env="LIVE")
    assert out == {"policy": None}


def test_put_user_style_without_owner_still_strict(cal_db, monkeypatch, bridge_pkg):
    """Writes still require an owner id (no silent global personal style)."""
    _ = bridge_pkg
    import pytest as _pytest

    from fastapi import HTTPException

    plugin_api = _load_plugin_api()
    monkeypatch.setattr(plugin_api, "_require_bridge_key", lambda _k: None)
    body = plugin_api.PolicyPutBody(
        content_md="x", updated_by="t", env="LIVE", owner_user_id=None,
    )
    with _pytest.raises(HTTPException) as exc:
        plugin_api.put_policy("user_style", body, x_bridge_key=None)
    assert exc.value.status_code == 400


def test_cli_persist_initial_outreach_help():
    proc = subprocess.run(
        [sys.executable, str(_CLI), "persist-initial-outreach-draft", "--help"],
        cwd=_PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "initial-outreach" in proc.stdout or "reply-drafts/persist" in proc.stdout
