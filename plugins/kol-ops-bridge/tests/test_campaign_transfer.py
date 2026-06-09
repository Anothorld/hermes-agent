"""Shortlist campaign transfer (Phase 1a)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_pkg"


def _load_transfer_module(cal_mod):
    pkg = sys.modules.get(_PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(_PLUGIN_ROOT)]
        sys.modules[_PKG_NAME] = pkg
        pkg.cal = cal_mod  # type: ignore[attr-defined]
    spec = importlib.util.spec_from_file_location(
        f"{_PKG_NAME}.campaign_transfer",
        _PLUGIN_ROOT / "campaign_transfer.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG_NAME}.campaign_transfer"] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed(cal, *, campaign_id: str, handle: str, status: str = "discovered") -> int:
    cal.upsert_campaign_config(campaign_id=campaign_id, env="LIVE")
    iid = cal.upsert_identity(
        primary_handle=handle,
        primary_email=f"{handle.lstrip('@')}@example.com",
        env="LIVE",
    )
    cal.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=iid,
        source="test",
        candidate_status=status,
        env="LIVE",
        enforce_outreach_cooldown=False,
        enforce_discovery_skip=False,
    )
    return iid


def test_transfer_shortlist_moves_candidate(cal_db):
    transfer = _load_transfer_module(cal_db)
    src, dst = "XFER-SRC", "XFER-DST"
    cal_db.upsert_campaign_config(campaign_id=dst, env="LIVE")
    iid = _seed(cal_db, campaign_id=src, handle="@move_me")

    out = transfer.transfer_shortlist_candidate(
        identity_id=iid,
        from_campaign_id=src,
        to_campaign_id=dst,
        env="LIVE",
        reason="better fit",
    )

    assert out["target_candidate_status"] == "discovered"
    src_row = cal_db.get_candidate_for(identity_id=iid, campaign_id=src, env="LIVE")
    dst_row = cal_db.get_candidate_for(identity_id=iid, campaign_id=dst, env="LIVE")
    assert src_row["candidate_status"] == "rejected"
    assert "transferred_to:XFER-DST" in (src_row.get("review_reason") or "")
    assert dst_row["candidate_status"] == "discovered"
    assert dst_row["source"] == "operator_transfer"


def test_transfer_rejects_same_campaign(cal_db):
    transfer = _load_transfer_module(cal_db)
    cid = "XFER-SAME"
    iid = _seed(cal_db, campaign_id=cid, handle="@same")
    with pytest.raises(transfer.CampaignTransferError) as exc:
        transfer.transfer_shortlist_candidate(
            identity_id=iid,
            from_campaign_id=cid,
            to_campaign_id=cid,
            env="LIVE",
        )
    assert exc.value.code == "same_campaign"


def test_transfer_rejects_approved_source(cal_db):
    transfer = _load_transfer_module(cal_db)
    src, dst = "XFER-APP-SRC", "XFER-APP-DST"
    cal_db.upsert_campaign_config(campaign_id=dst, env="LIVE")
    iid = _seed(cal_db, campaign_id=src, handle="@approved", status="selected_for_outreach")
    with pytest.raises(transfer.CampaignTransferError) as exc:
        transfer.transfer_shortlist_candidate(
            identity_id=iid,
            from_campaign_id=src,
            to_campaign_id=dst,
            env="LIVE",
        )
    assert exc.value.code == "source_not_shortlist"


def test_transfer_blocks_existing_target_row(cal_db):
    transfer = _load_transfer_module(cal_db)
    src, dst = "XFER-BLK-SRC", "XFER-BLK-DST"
    iid = _seed(cal_db, campaign_id=src, handle="@blocked")
    _seed(cal_db, campaign_id=dst, handle="@blocked")
    with pytest.raises(transfer.CampaignTransferError) as exc:
        transfer.transfer_shortlist_candidate(
            identity_id=iid,
            from_campaign_id=src,
            to_campaign_id=dst,
            env="LIVE",
        )
    assert exc.value.code == "target_candidate_exists"
