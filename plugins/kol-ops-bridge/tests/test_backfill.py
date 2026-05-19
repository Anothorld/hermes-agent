"""Unit test for the backfill helpers — exercises the parsing + CAL writes
without depending on a real Kanban DB."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_backfill(plugin_root: Path):
    """Load backfill module under a synthetic package so it can be re-loaded
    per-test alongside the cal module that the conftest already loaded."""
    pkg_name = "kol_ops_bridge_pkg"
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.scripts.backfill",
        plugin_root / "scripts" / "backfill.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.scripts.backfill"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_backfill_writes_identity_alias_event(cal_db, monkeypatch):
    plugin_root = Path(cal_db.__file__).resolve().parent
    backfill = _load_backfill(plugin_root)
    # Force backfill to use the same cal module the fixture already pointed at a temp DB.
    monkeypatch.setattr(backfill, "_load_cal", lambda: cal_db)

    fake_cards = [
        {
            "_task_id": "t-1",
            "kol_handle": "kathy",
            "email": "k@x.com",
            "gmail_thread_id": "thread-1",
            "campaign_id": "seb8008-spring",
            "product_sku": "SEB8008",
            "creator_type": "micro",
            "selling_point_group": "A",
            "stage": "outreach",
            "sub_status": "initial_drafted",
            "status": "drafted_initial",
            "draft_ids": {"initial": "r-100", "product_pitch": None},
        }
    ]
    monkeypatch.setattr(backfill, "_iter_kol_cards", lambda board: fake_cards)

    stats = backfill.backfill(board="kol-outreach", env="LIVE", dry_run=False)
    assert stats["cards"] == 1
    assert stats["identities"] == 1
    assert stats["aliases"] == 3  # email + thread + handle
    assert stats["events"] == 2  # backfilled + backfilled_draft:initial

    # Re-run: identity upsert + aliases are idempotent; draft event is NOT
    # re-emitted because get_draft is still None — that's OK, idempotency
    # for the placeholder draft event is best-effort.
    stats2 = backfill.backfill(board="kol-outreach", env="LIVE", dry_run=False)
    assert stats2["identities"] == 1  # same id returned, counted as upsert


def test_parse_body_handles_fenced_yaml(cal_db):
    plugin_root = Path(cal_db.__file__).resolve().parent
    backfill = _load_backfill(plugin_root)
    fenced = "```yaml\nkol_handle: kathy\nemail: k@x.com\n```"
    parsed = backfill._parse_body(fenced)
    assert parsed == {"kol_handle": "kathy", "email": "k@x.com"}


def test_parse_body_returns_none_on_garbage(cal_db):
    plugin_root = Path(cal_db.__file__).resolve().parent
    backfill = _load_backfill(plugin_root)
    assert backfill._parse_body(": : :") is None
