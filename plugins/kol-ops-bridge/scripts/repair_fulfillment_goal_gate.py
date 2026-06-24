#!/usr/bin/env python3
"""Repair fulfillment goals that activated early when contract_required=false.

Root cause: ``_contract_satisfied`` used to return True whenever contracts were
skipped, even before ``offer.agreed_terms``. This script:

1. Optionally clears agent-miswritten ``identity.followup_*`` facts (712 case).
2. Re-runs ``recompute_goals`` for affected identity/campaign rows.

Usage:
  python3 plugins/kol-ops-bridge/scripts/repair_fulfillment_goal_gate.py \\
    --identity-id 712 --campaign-id POVISON-TS-8319-20260603

  python3 plugins/kol-ops-bridge/scripts/repair_fulfillment_goal_gate.py --scan

Env:
  KOL_REPAIR_ENV  LIVE|TEST (default LIVE)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_repair_pkg"
_DEBUG_LOG = Path("/Users/arnold/agent_prj/.cursor/debug-cf0831.log")

_SPURIOUS_IDENTITY_KEYS = (
    "identity.followup_sent",
    "identity.followup_reason",
    "identity.awaiting_response_from",
    "identity.previous_action_required",
)


def _load_cal():
    if _PKG in sys.modules:
        return sys.modules[_PKG].cal
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = pkg
    for sub in ("schema", "goals", "implicit_accept_policy", "cal"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{sub}", _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg.cal


def _debug_log(message: str, data: dict) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "cf0831",
            "hypothesisId": "H4",
            "location": "repair_fulfillment_goal_gate.py",
            "message": message,
            "data": data,
            "timestamp": int(__import__("time").time() * 1000),
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _goal_snapshot(cal, identity_id: int, campaign_id: str, env: str) -> dict[str, str]:
    rows = cal.get_goal_state(identity_id=identity_id, campaign_id=campaign_id, env=env)
    return {r["goal"]: r["status"] for r in rows}


def _clear_spurious_identity_facts(
    cal,
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    dry_run: bool,
) -> list[str]:
    cleared: list[str] = []
    latest = cal.latest_facts_for(identity_id=identity_id, campaign_id=campaign_id, env=env)
    to_write: dict[str, bool] = {}
    for key in _SPURIOUS_IDENTITY_KEYS:
        if latest.get(key):
            to_write[key] = False
            cleared.append(key)
    if to_write and not dry_run:
        cal.write_facts(
            identity_id=identity_id,
            campaign_id=campaign_id,
            namespace="identity",
            facts=to_write,
            source="repair:fulfillment_goal_gate",
            env=env,
        )
    return cleared


def repair_one(
    cal,
    *,
    identity_id: int,
    campaign_id: str,
    env: str,
    dry_run: bool,
    clear_spurious: bool,
) -> dict:
    before = _goal_snapshot(cal, identity_id, campaign_id, env)
    cleared = (
        _clear_spurious_identity_facts(
            cal,
            identity_id=identity_id,
            campaign_id=campaign_id,
            env=env,
            dry_run=dry_run,
        )
        if clear_spurious
        else []
    )
    if not dry_run:
        cal.recompute_goals(identity_id=identity_id, campaign_id=campaign_id, env=env)
    after = before if dry_run else _goal_snapshot(cal, identity_id, campaign_id, env)
    result = {
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": env,
        "dry_run": dry_run,
        "cleared_identity_facts": cleared,
        "before": {k: before[k] for k in ("logistics", "payout_setup", "deliverables_scope", "compensation_negotiation") if k in before},
        "after": {k: after[k] for k in ("logistics", "payout_setup", "deliverables_scope", "compensation_negotiation") if k in after},
    }
    _debug_log("repair_one_complete", result)
    return result


def scan_early_fulfillment(cal, *, env: str) -> list[tuple[int, str]]:
    """Find rows with active fulfillment but no agreed_terms (pre-fix snapshot)."""
    db_path = cal.db_path()
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT gs.identity_id, gs.campaign_id
          FROM kol_goal_state gs
          JOIN campaign_config cc ON cc.campaign_id = gs.campaign_id
         WHERE gs.env = ?
           AND cc.contract_required = 0
           AND gs.goal IN ('logistics', 'payout_setup')
           AND gs.status = 'active'
         GROUP BY gs.identity_id, gs.campaign_id
        """,
        (env,),
    ).fetchall()
    out: list[tuple[int, str]] = []
    for row in rows:
        iid = int(row["identity_id"])
        cid = str(row["campaign_id"])
        facts = cal.latest_facts_for(identity_id=iid, campaign_id=cid, env=env)
        if not facts.get("offer.agreed_terms"):
            out.append((iid, cid))
    conn.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-id", type=int)
    parser.add_argument("--campaign-id")
    parser.add_argument("--env", default=os.environ.get("KOL_REPAIR_ENV", "LIVE"))
    parser.add_argument("--scan", action="store_true", help="Scan and repair all affected rows")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-clear-spurious",
        action="store_true",
        help="Skip clearing identity.followup_* repair facts",
    )
    args = parser.parse_args()
    cal = _load_cal()
    env = args.env.upper()
    clear_spurious = not args.no_clear_spurious

    targets: list[tuple[int, str]] = []
    if args.scan:
        targets = scan_early_fulfillment(cal, env=env)
        print(f"scan found {len(targets)} identity/campaign pairs")
    elif args.identity_id and args.campaign_id:
        targets = [(args.identity_id, args.campaign_id)]
    else:
        parser.error("Provide --identity-id + --campaign-id or --scan")

    results = [
        repair_one(
            cal,
            identity_id=iid,
            campaign_id=cid,
            env=env,
            dry_run=args.dry_run,
            clear_spurious=clear_spurious,
        )
        for iid, cid in targets
    ]
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
