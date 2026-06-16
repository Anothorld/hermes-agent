#!/usr/bin/env python3
"""Clean up kol-reply retry-storm artifacts (zombie sessions + poller stop).

Dry-run by default. Use ``--apply`` to write backups and apply changes.

What it does:
1. Backs up ``state.db`` and ``poller_state.json`` (profile + bridge-root if present).
2. Deletes kol-reply **zombie** sessions (``api_call_count=0``, ``ended_at IS NULL``)
   and their messages from SessionDB.
3. Writes ``kol_reply_dispatch_exhausted`` CAL events for inbound messages that still
   have ``should_retry_gateway_only`` semantics (no draft / handled / prior exhausted).
4. Sets ``gateway_only_retries_{env}`` to the cap in poller_state for those messages.

Does **not** delete CAL business facts (``approval.reply_draft``, etc.).

Usage::

    HERMES_HOME=~/.hermes/profiles/kol-orchestrator \\
      python plugins/kol-ops-bridge/scripts/cleanup_inbound_reply_storm.py

    # Apply:
    HERMES_HOME=~/.hermes/profiles/kol-orchestrator \\
      python plugins/kol-ops-bridge/scripts/cleanup_inbound_reply_storm.py --apply

    # Limit to specific identities:
    ... --apply --identity-ids 751,689
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT.parent))


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_cal_db() -> Path:
    p = os.environ.get("HERMES_KOL_OPS_CAL_DB")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".hermes" / "kol-ops-bridge" / "cal.db"


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def _state_db_path() -> Path:
    explicit = os.environ.get("HERMES_STATE_DB")
    if explicit:
        return Path(explicit).expanduser()
    return _hermes_home() / "state.db"


def _poller_state_paths() -> list[Path]:
    paths = [
        _hermes_home() / "kol-ops-bridge" / "poller_state.json",
        Path.home() / ".hermes" / "kol-ops-bridge" / "poller_state.json",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.exists():
            out.append(p)
    return out


def _load_cal_module():
    import importlib.util
    import types

    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_SCRIPT_ROOT)]
        sys.modules[pkg_name] = pkg
    for sub in ("schema", "gmail_thread_resolve", "reply_draft", "reply_chase", "cal"):
        full = f"{pkg_name}.{sub}"
        if full in sys.modules:
            continue
        path = _SCRIPT_ROOT / f"{sub}.py"
        spec = importlib.util.spec_from_file_location(full, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        setattr(sys.modules[pkg_name], sub, mod)
        spec.loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.cal"]


def find_stuck_inbound_messages(
    cal_mod: Any,
    *,
    env: str,
    identity_ids: Optional[set[int]],
) -> list[dict[str, Any]]:
    """Inbound messages that would still gateway-only retry."""

    conn = sqlite3.connect(str(_default_cal_db()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT identity_id, campaign_id, payload_json
             FROM kol_conversation_events
            WHERE env=? AND event_type='kol_inbound_reply'
            ORDER BY id""",
        (env,),
    ).fetchall()
    conn.close()

    stuck: list[dict[str, Any]] = []
    seen_mids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        mid = str(payload.get("message_id") or "").strip()
        if not mid or mid in seen_mids:
            continue
        ident = int(row["identity_id"])
        if identity_ids and ident not in identity_ids:
            continue
        cid = row["campaign_id"]
        status = cal_mod.reply_dispatch_status(
            identity_id=ident,
            campaign_id=cid,
            message_id=mid,
            env=env,
        )
        if status.get("should_retry_gateway_only"):
            seen_mids.add(mid)
            stuck.append({
                "identity_id": ident,
                "campaign_id": cid,
                "message_id": mid,
                "chase_action": status.get("chase_action"),
            })
    return stuck


def count_zombie_sessions(
    state_db: Path,
    *,
    identity_ids: Optional[set[int]],
) -> int:
    conn = sqlite3.connect(str(state_db))
    if identity_ids:
        clauses = " OR ".join(
            f"id LIKE 'kol-reply:%:{i}:%'" for i in sorted(identity_ids)
        )
        sql = f"""
            SELECT COUNT(*) FROM sessions
            WHERE ({clauses})
              AND api_call_count=0 AND ended_at IS NULL
        """
        c = conn.execute(sql).fetchone()[0]
    else:
        c = conn.execute(
            """SELECT COUNT(*) FROM sessions
                WHERE id LIKE 'kol-reply:%'
                  AND api_call_count=0 AND ended_at IS NULL""",
        ).fetchone()[0]
    conn.close()
    return int(c)


def delete_zombie_sessions(
    state_db: Path,
    *,
    identity_ids: Optional[set[int]],
    apply: bool,
) -> int:
    conn = sqlite3.connect(str(state_db))
    if identity_ids:
        id_filter = " OR ".join(
            f"s.id LIKE 'kol-reply:%:{i}:%'" for i in sorted(identity_ids)
        )
        where = f"({id_filter}) AND s.api_call_count=0 AND s.ended_at IS NULL"
    else:
        where = "s.id LIKE 'kol-reply:%' AND s.api_call_count=0 AND s.ended_at IS NULL"

    count = conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE {where.replace('s.', '')}",
    ).fetchone()[0]
    if apply and count:
        where_plain = where.replace("s.", "")
        conn.execute(
            f"""DELETE FROM messages
                 WHERE session_id IN (SELECT id FROM sessions WHERE {where_plain})""",
        )
        conn.execute(f"DELETE FROM sessions WHERE {where_plain}")
        conn.commit()
    conn.close()
    return int(count)


def write_dispatch_exhausted(
    cal_mod: Any,
    stuck: list[dict[str, Any]],
    *,
    env: str,
    retry_cap: int,
    apply: bool,
) -> int:
    written = 0
    for row in stuck:
        mid = row["message_id"]
        ident = row["identity_id"]
        cid = row["campaign_id"]
        status = cal_mod.reply_dispatch_status(
            identity_id=ident, campaign_id=cid, message_id=mid, env=env,
        )
        if status.get("has_dispatch_exhausted_event"):
            continue
        if apply:
            cal_mod.write_event(
                identity_id=ident,
                campaign_id=cid,
                event_type="kol_reply_dispatch_exhausted",
                actor="bridge:cleanup-inbound-reply-storm",
                payload={"message_id": mid, "retry_cap": retry_cap, "reason": "storm_cleanup"},
                env=env,
            )
        written += 1
    return written


def patch_poller_state(
    paths: list[Path],
    stuck: list[dict[str, Any]],
    *,
    env: str,
    retry_cap: int,
    apply: bool,
) -> list[str]:
    key = f"gateway_only_retries_{env}"
    backoff_key = f"retry_backoff_{env}"
    failures_key = f"retry_failures_{env}"
    notes: list[str] = []
    for path in paths:
        state = json.loads(path.read_text(encoding="utf-8"))
        bucket = state.setdefault(key, {})
        if not isinstance(bucket, dict):
            bucket = {}
            state[key] = bucket
        for row in stuck:
            mid = row["message_id"]
            bucket[mid] = retry_cap
            if isinstance(state.get(backoff_key), dict):
                state[backoff_key].pop(mid, None)
            if isinstance(state.get(failures_key), dict):
                state[failures_key].pop(mid, None)
        notes.append(f"{path}: set {key} for {len(stuck)} message(s)")
        if apply:
            backup = path.with_suffix(f".json.bak-{_now_stamp()}")
            shutil.copy2(path, backup)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
    return notes


def backup_file(path: Path, apply: bool) -> Optional[Path]:
    if not path.exists() or not apply:
        return None
    dest = path.with_suffix(path.suffix + f".bak-{_now_stamp()}")
    shutil.copy2(path, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument("--env", default="LIVE", choices=("LIVE", "TEST"))
    parser.add_argument(
        "--identity-ids",
        default="",
        help="Comma-separated identity ids to scope cleanup (default: all)",
    )
    parser.add_argument(
        "--retry-cap",
        type=int,
        default=int(os.environ.get("KOL_OPS_INBOUND_GATEWAY_ONLY_RETRY_MAX", "8")),
    )
    args = parser.parse_args()

    identity_ids: Optional[set[int]] = None
    if args.identity_ids.strip():
        identity_ids = {int(x.strip()) for x in args.identity_ids.split(",") if x.strip()}

    cal_mod = _load_cal_module()
    os.environ.setdefault("HERMES_KOL_OPS_CAL_DB", str(_default_cal_db()))

    state_db = _state_db_path()
    poller_paths = _poller_state_paths()
    stuck = find_stuck_inbound_messages(cal_mod, env=args.env, identity_ids=identity_ids)
    zombies = count_zombie_sessions(state_db, identity_ids=identity_ids)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== cleanup_inbound_reply_storm [{mode}] ===")
    print(f"HERMES_HOME={_hermes_home()}")
    print(f"state.db={state_db}")
    print(f"cal.db={_default_cal_db()}")
    print(f"poller_state files: {poller_paths or '(none)'}")
    print(f"stuck inbound messages (would exhaust): {len(stuck)}")
    for row in stuck[:20]:
        print(f"  LIVE:{row['identity_id']} {row['message_id'][:16]}... chase={row.get('chase_action')}")
    if len(stuck) > 20:
        print(f"  ... and {len(stuck) - 20} more")
    print(f"zombie kol-reply sessions to delete: {zombies}")

    if args.apply:
        b = backup_file(state_db, True)
        if b:
            print(f"backup state.db -> {b}")

    deleted = delete_zombie_sessions(state_db, identity_ids=identity_ids, apply=args.apply)
    exhausted = write_dispatch_exhausted(
        cal_mod, stuck, env=args.env, retry_cap=args.retry_cap, apply=args.apply,
    )
    poller_notes = patch_poller_state(
        poller_paths, stuck, env=args.env, retry_cap=args.retry_cap, apply=args.apply,
    )

    print(f"\n{'Applied' if args.apply else 'Would apply'}:")
    print(f"  deleted zombie sessions: {deleted}")
    print(f"  dispatch_exhausted events written: {exhausted}")
    for note in poller_notes:
        print(f"  {note}")

    if not args.apply:
        print("\nRe-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
