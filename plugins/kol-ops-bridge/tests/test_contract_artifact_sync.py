"""Tests for post-render CAL artifact sync."""

from __future__ import annotations

from pathlib import Path

from kol_ops_bridge_pkg import contract_artifacts as ca


def test_build_render_sync_namespaces_skips_redacted_agent_body():
    path = Path("/tmp/contracts/new.docx")
    facts = {
        "approval.reply_draft": {
            "draft": {
                "body": ca.REDACTED_AGENT_BODY,
                "attachments": ["/tmp/old.docx"],
            },
        },
    }
    namespaces = ca.build_render_sync_namespaces(path, facts)
    assert namespaces["offer"]["offer.contract_artifact_path"] == str(path)
    assert "approval" not in namespaces


def test_patch_reply_draft_attachment_only_changes_attachments():
    draft = {
        "draft": {
            "body": "Hi Megan,\n\nPlease review the agreement.",
            "attachments": ["/tmp/old.docx"],
            "to": "a@b.com",
        },
    }
    out = ca.patch_reply_draft_attachment(draft, "/tmp/new.docx")
    assert out["draft"]["attachments"] == ["/tmp/new.docx"]
    assert out["draft"]["body"] == draft["draft"]["body"]
    assert draft["draft"]["attachments"] == ["/tmp/old.docx"]


def test_build_render_sync_namespaces_updates_draft_attachment():
    path = Path("/tmp/contracts/POVISON_Influencer_Agreement_Megan_McLeod_SEB8008_20260617.docx")
    facts = {
        "approval.reply_draft": {
            "draft": {
                "body": "Hi Megan,\n\nAttached is our agreement.",
                "attachments": [
                    "/tmp/old/POVISON_Influencer_Agreement_Megan_McLeod_SEB8008_20260616.docx",
                ],
            },
        },
    }
    namespaces = ca.build_render_sync_namespaces(path, facts)
    assert namespaces["offer"]["offer.contract_artifact_path"] == str(path)
    assert namespaces["approval"]["approval.reply_draft"]["draft"]["attachments"] == [str(path)]
    assert "Attached" in namespaces["approval"]["approval.reply_draft"]["draft"]["body"]
