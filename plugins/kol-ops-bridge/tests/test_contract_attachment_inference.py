"""Tests for contract attachment inference on persist/approve."""

from __future__ import annotations

from kol_ops_bridge_pkg import contract_artifacts as ca


def test_infer_from_contract_artifact_when_draft_missing_attachments():
    facts = {
        "offer.contract_artifact_path": "/tmp/contracts/LIVE/camp/file.docx",
    }
    paths = ca.infer_draft_attachment_paths(
        merged_or_draft={"body": "Please sign"},
        facts=facts,
        primary_goal="contract_signing",
    )
    assert paths == ["/tmp/contracts/LIVE/camp/file.docx"]


def test_infer_preserves_prior_pending_attachments():
    prior = {
        "draft": {
            "attachments": ["/tmp/old.docx"],
        },
    }
    paths = ca.infer_draft_attachment_paths(
        merged_or_draft={"body": "Updated body"},
        facts={},
        prior_approval=prior,
        primary_goal="contract_signing",
    )
    assert paths == ["/tmp/old.docx"]


def test_infer_prefers_explicit_child_attachments():
    paths = ca.infer_draft_attachment_paths(
        merged_or_draft={"attachments": ["/tmp/explicit.docx"]},
        facts={"offer.contract_artifact_path": "/tmp/other.docx"},
        primary_goal="contract_signing",
    )
    assert paths == ["/tmp/explicit.docx"]
