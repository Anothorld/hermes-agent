#!/usr/bin/env python3
"""Merge two campaigns of one product into a single canonical campaign.

One-product-one-campaign migration tool. For each (source → target) pair it:

1. Backs up the bridge CAL DB and the console app DB (``.bak-<ts>`` copies).
2. Calls bridge ``POST /campaigns/{target}/merge-from`` (moves candidates,
   goal states, facts, events, escalations; target rows win on conflicts;
   deletes the source ``campaign_config``).
3. Console DB: re-points ``product_campaign_runs`` to the target, deletes the
   source ``product_campaigns`` row, and writes an ``campaign.merge`` audit row.

Usage:
    python3 merge_campaigns.py --source SEB8010-20260610 \
        --target SEB8010-20260608 --env LIVE [--dry-run]

Reads bridge base/key from the console ``.env`` (KOC_BRIDGE_BASE /
KOC_BRIDGE_KEY) unless overridden by flags.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import sys
import urllib.request
from pathlib import Path

_CONSOLE_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_APP_DB = Path.home() / ".hermes" / "kol-ops-console" / "app.db"
_DEFAULT_CAL_DB = Path.home() / ".hermes" / "kol-ops-bridge" / "cal.db"


def _read_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    env_path = _CONSOLE_ROOT / ".env"
    if not env_path.is_file():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _backup(path: Path, ts: str) -> Path | None:
    if not path.is_file():
        return None
    dest = path.with_name(f"{path.name}.bak-{ts}")
    shutil.copy2(path, dest)
    return dest


def _bridge_merge(base: str, key: str, *, source: str, target: str, env: str) -> dict:
    url = f"{base.rstrip('/')}/campaigns/{target}/merge-from"
    body = json.dumps({"source_campaign_id": source, "env": env}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Bridge-Key": key},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _console_merge(
    conn: sqlite3.Connection, *, source: str, target: str, env: str,
    bridge_summary: dict,
) -> dict:
    runs = conn.execute(
        "UPDATE product_campaign_runs SET campaign_id=? WHERE campaign_id=? AND env=?",
        (target, source, env),
    ).rowcount
    deleted = conn.execute(
        "DELETE FROM product_campaigns WHERE campaign_id=? AND env=?",
        (source, env),
    ).rowcount
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, payload_json, ts) "
        "VALUES (NULL, 'campaign.merge', ?, ?, ?)",
        (
            target,
            json.dumps(
                {
                    "source_campaign_id": source,
                    "env": env,
                    "runs_repointed": runs,
                    "source_row_deleted": deleted,
                    "bridge": bridge_summary,
                },
                ensure_ascii=False,
            ),
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return {"runs_repointed": runs, "source_row_deleted": deleted}


def main() -> int:
    env_file = _read_env_file()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="campaign_id to dissolve")
    ap.add_argument("--target", required=True, help="surviving campaign_id")
    ap.add_argument("--env", default="LIVE", choices=("LIVE", "TEST"))
    ap.add_argument("--app-db", type=Path, default=_DEFAULT_APP_DB)
    ap.add_argument("--cal-db", type=Path, default=_DEFAULT_CAL_DB,
                    help="Only used for the pre-merge backup copy.")
    ap.add_argument("--bridge-base",
                    default=env_file.get("KOC_BRIDGE_BASE", ""))
    ap.add_argument("--bridge-key",
                    default=env_file.get("KOC_BRIDGE_KEY", ""))
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would happen; change nothing.")
    args = ap.parse_args()

    if args.source == args.target:
        print("error: source and target must differ", file=sys.stderr)
        return 2
    if not args.bridge_base or not args.bridge_key:
        print("error: bridge base/key missing (flags or console .env)", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.app_db)
    conn.row_factory = sqlite3.Row
    rows = {
        r["campaign_id"]: dict(r)
        for r in conn.execute(
            "SELECT campaign_id, sku, status FROM product_campaigns "
            "WHERE campaign_id IN (?, ?) AND env=?",
            (args.source, args.target, args.env),
        )
    }
    if args.target not in rows:
        print(f"error: target {args.target} not in product_campaigns", file=sys.stderr)
        return 2
    src_row = rows.get(args.source)
    if src_row and src_row["sku"] != rows[args.target]["sku"]:
        print(
            f"error: sku mismatch — source={src_row['sku']} "
            f"target={rows[args.target]['sku']}; refusing cross-product merge",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(json.dumps({
            "would_merge": {"source": args.source, "target": args.target, "env": args.env},
            "console_rows": rows,
        }, ensure_ascii=False, indent=2))
        return 0

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups = {
        "app_db": str(_backup(args.app_db, ts) or "missing"),
        "cal_db": str(_backup(args.cal_db, ts) or "missing"),
    }
    print(f"backups: {json.dumps(backups)}")

    bridge_summary = _bridge_merge(
        args.bridge_base, args.bridge_key,
        source=args.source, target=args.target, env=args.env,
    )
    print(f"bridge: {json.dumps(bridge_summary, ensure_ascii=False)}")

    console_summary = _console_merge(
        conn, source=args.source, target=args.target, env=args.env,
        bridge_summary=bridge_summary,
    )
    print(f"console: {json.dumps(console_summary, ensure_ascii=False)}")
    print("done — restart console backend so cached campaign lists refresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
