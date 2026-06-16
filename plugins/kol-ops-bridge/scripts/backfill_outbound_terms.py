#!/usr/bin/env python3
"""Backfill ``offer.last_outbound_terms_proposed`` from CAL ``outbound_sent`` events.

Uses ``~/.hermes/kol-ops-bridge/cal.db`` via CAL — no ad-hoc SQL.

Environment:
  KOL_BACKFILL_ENV       TEST|LIVE (default LIVE)
  KOL_BACKFILL_DRY_RUN   1|true to preview only
  KOL_BACKFILL_CAMPAIGN  optional campaign_id filter
  KOL_BACKFILL_IDENTITY  optional identity_id filter
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_outbound_backfill_pkg"


def _load_pkg() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = pkg
    for sub in ("schema", "cal", "implicit_accept_policy"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{sub}", _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


def backfill(
    *,
    env: str,
    dry_run: bool,
    campaign_id: str | None,
    identity_id: int | None,
) -> dict:
    pkg = _load_pkg()
    cal = pkg.cal
    iap = pkg.implicit_accept_policy
    scanned = 0
    updated = 0
    skipped = 0
    samples: list[dict] = []

    campaigns = (
        [campaign_id]
        if campaign_id
        else [c["campaign_id"] for c in cal.list_campaigns(env=env)]
    )
    for cid in campaigns:
        if not cid:
            continue
        ids = [identity_id] if identity_id is not None else None
        if ids is None:
            cands = cal.list_candidates(cid, env=env)
            ids = [int(c["identity_id"]) for c in cands if c.get("identity_id")]
        for iid in ids or []:
            scanned += 1
            state = cal.latest_facts_for(
                identity_id=iid, campaign_id=cid, env=env,
            )
            if state.get("offer.last_outbound_terms_proposed"):
                skipped += 1
                continue
            events = cal.list_outbound_sent_events(
                identity_id=iid, campaign_id=cid, env=env, limit=50,
            )
            bodies = iap.extract_outbound_bodies(events)
            body = ""
            for candidate in bodies:
                if iap.brand_proposed_terms({}, {"outbound_bodies": [candidate]}):
                    body = candidate
                    break
            if not body:
                skipped += 1
                continue
            if dry_run:
                updated += 1
                if len(samples) < 5:
                    samples.append(
                        {"identity_id": iid, "campaign_id": cid, "chars": len(body)},
                    )
                continue
            cal.write_facts(
                identity_id=iid,
                campaign_id=cid,
                namespace="offer",
                facts={"offer.last_outbound_terms_proposed": body},
                source="script:backfill_outbound_terms",
                env=env,
            )
            updated += 1
    return {
        "env": env,
        "dry_run": dry_run,
        "scanned": scanned,
        "updated": updated,
        "skipped": skipped,
        "samples": samples,
    }


def main() -> int:
    env = (os.environ.get("KOL_BACKFILL_ENV") or "LIVE").strip().upper()
    dry_run = os.environ.get("KOL_BACKFILL_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    campaign_id = (os.environ.get("KOL_BACKFILL_CAMPAIGN") or "").strip() or None
    identity_raw = (os.environ.get("KOL_BACKFILL_IDENTITY") or "").strip()
    identity_id = int(identity_raw) if identity_raw.isdigit() else None
    result = backfill(
        env=env,
        dry_run=dry_run,
        campaign_id=campaign_id,
        identity_id=identity_id,
    )
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
