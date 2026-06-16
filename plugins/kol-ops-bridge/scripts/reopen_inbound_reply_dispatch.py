#!/usr/bin/env python3
"""Clear ``kol_reply_dispatch_exhausted`` and trigger one gateway dispatch.

Dry-run by default. Use ``--apply`` to write CAL/poller changes and dispatch.

Usage::

    HERMES_HOME=~/.hermes/profiles/kol-orchestrator \\
      python plugins/kol-ops-bridge/scripts/reopen_inbound_reply_dispatch.py \\
        --identity-ids 751,689 --apply
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


def _poller_state_paths() -> list[Path]:
    paths = [
        _hermes_home() / "kol-ops-bridge" / "poller_state.json",
        Path.home() / ".hermes" / "kol-ops-bridge" / "poller_state.json",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen or not p.exists():
            continue
        seen.add(key)
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


def resolve_targets(
    *,
    env: str,
    identity_ids: set[int],
    message_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(_default_cal_db()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT identity_id, campaign_id, payload_json
             FROM kol_conversation_events
            WHERE env=? AND event_type='kol_inbound_reply'
              AND identity_id IN ({})
            ORDER BY id""".format(",".join("?" * len(identity_ids))),
        [env, *sorted(identity_ids)],
    ).fetchall()
    conn.close()

    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        mid = str(payload.get("message_id") or "").strip()
        if not mid or mid in seen:
            continue
        if message_ids is not None and mid not in message_ids:
            continue
        seen.add(mid)
        targets.append({
            "identity_id": int(row["identity_id"]),
            "campaign_id": row["campaign_id"],
            "message_id": mid,
            "mailbox_user_id": payload.get("detected_mailbox_user_id"),
            "mailbox_email": payload.get("detected_mailbox_email"),
        })
    return targets


def clear_dispatch_exhausted(
    targets: list[dict[str, Any]],
    *,
    env: str,
    apply: bool,
) -> int:
    conn = sqlite3.connect(str(_default_cal_db()))
    removed = 0
    for t in targets:
        cur = conn.execute(
            """SELECT id FROM kol_conversation_events
                WHERE env=? AND identity_id=? AND campaign_id=?
                  AND event_type='kol_reply_dispatch_exhausted'
                  AND json_extract(payload_json,'$.message_id')=?""",
            (env, t["identity_id"], t["campaign_id"], t["message_id"]),
        ).fetchall()
        removed += len(cur)
        if apply and cur:
            conn.execute(
                """DELETE FROM kol_conversation_events
                    WHERE env=? AND identity_id=? AND campaign_id=?
                      AND event_type='kol_reply_dispatch_exhausted'
                      AND json_extract(payload_json,'$.message_id')=?""",
                (env, t["identity_id"], t["campaign_id"], t["message_id"]),
            )
    if apply:
        conn.commit()
    conn.close()
    return removed


def clear_poller_retries(
    targets: list[dict[str, Any]],
    *,
    env: str,
    apply: bool,
) -> list[str]:
    key = f"gateway_only_retries_{env}"
    backoff_key = f"retry_backoff_{env}"
    failures_key = f"retry_failures_{env}"
    notes: list[str] = []
    for path in _poller_state_paths():
        state = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for t in targets:
            mid = t["message_id"]
            for bucket_key in (key, backoff_key, failures_key):
                bucket = state.get(bucket_key)
                if isinstance(bucket, dict) and mid in bucket:
                    bucket.pop(mid, None)
                    changed = True
        if changed:
            notes.append(f"{path}: cleared retry counters for {len(targets)} message(s)")
            if apply:
                backup = path.with_suffix(f".json.bak-{_now_stamp()}")
                shutil.copy2(path, backup)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
                tmp.replace(path)
    return notes


def dispatch_targets(
    targets: list[dict[str, Any]],
    *,
    env: str,
    apply: bool,
) -> list[dict[str, Any]]:
    if not apply:
        return [{"message_id": t["message_id"], "status": "dry-run"} for t in targets]

    from kol_ops_bridge_pkg.gmail_client import GmailUnavailable
    from kol_ops_bridge_pkg.gmail_console import list_operator_gmail_clients
    from kol_ops_bridge_pkg.inbound_reply.deps import InboundDeps
    from kol_ops_bridge_pkg.inbound_reply.processor import process_message

    deps = InboundDeps.in_process_default()
    mailboxes = {mb.user_id: mb for mb in list_operator_gmail_clients()}
    results: list[dict[str, Any]] = []

    for t in targets:
        mb_uid = t.get("mailbox_user_id")
        mb = mailboxes.get(int(mb_uid)) if mb_uid is not None else None
        if mb is None and mailboxes:
            mb = next(iter(mailboxes.values()))
        if mb is None:
            results.append({
                "message_id": t["message_id"],
                "identity_id": t["identity_id"],
                "status": "error",
                "error": "no_gmail_mailbox",
            })
            continue
        try:
            msg = mb.client.get_message(t["message_id"])
        except GmailUnavailable as exc:
            results.append({
                "message_id": t["message_id"],
                "identity_id": t["identity_id"],
                "status": "error",
                "error": f"gmail_get_failed: {exc}",
            })
            continue
        outcome = process_message(
            msg,
            env=env,
            client=mb.client,
            deps=deps,
            mailbox_user_id=mb.user_id,
            mailbox_email=mb.google_email,
        )
        status = outcome.status if hasattr(outcome, "status") else str(outcome)
        results.append({
            "message_id": t["message_id"],
            "identity_id": t["identity_id"],
            "status": status,
            "gateway_only_retry": getattr(outcome, "gateway_only_retry", False),
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--env", default="LIVE", choices=("LIVE", "TEST"))
    parser.add_argument("--identity-ids", required=True, help="Comma-separated identity ids")
    parser.add_argument(
        "--message-ids",
        default="",
        help="Optional comma-separated Gmail message ids to scope reopen/dispatch",
    )
    parser.add_argument("--no-dispatch", action="store_true", help="Only reopen; do not gateway dispatch")
    args = parser.parse_args()

    identity_ids = {int(x.strip()) for x in args.identity_ids.split(",") if x.strip()}
    message_ids: Optional[set[str]] = None
    if args.message_ids.strip():
        message_ids = {x.strip() for x in args.message_ids.split(",") if x.strip()}
    cal_mod = _load_cal_module()
    os.environ.setdefault("HERMES_KOL_OPS_CAL_DB", str(_default_cal_db()))

    targets = resolve_targets(
        env=args.env,
        identity_ids=identity_ids,
        message_ids=message_ids,
    )
    if not targets:
        print("No inbound targets found for identity ids:", sorted(identity_ids))
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== reopen_inbound_reply_dispatch [{mode}] ===")
    for t in targets:
        st = cal_mod.reply_dispatch_status(
            identity_id=t["identity_id"],
            campaign_id=t["campaign_id"],
            message_id=t["message_id"],
            env=args.env,
        )
        print(
            f"  LIVE:{t['identity_id']} {t['message_id'][:16]}... "
            f"exhausted={st.get('has_dispatch_exhausted_event')} "
            f"retry={st.get('should_retry_gateway_only')}"
        )

    removed = clear_dispatch_exhausted(targets, env=args.env, apply=args.apply)
    poller_notes = clear_poller_retries(targets, env=args.env, apply=args.apply)
    dispatch_results: list[dict[str, Any]] = []
    if not args.no_dispatch:
        dispatch_results = dispatch_targets(targets, env=args.env, apply=args.apply)

    print(f"\n{'Applied' if args.apply else 'Would apply'}:")
    print(f"  dispatch_exhausted events removed: {removed}")
    for note in poller_notes:
        print(f"  {note}")
    if dispatch_results:
        print("  dispatch results:")
        for r in dispatch_results:
            print(f"    {r}")

    if args.apply:
        print("\nPost-reopen dispatch status:")
        for t in targets:
            st = cal_mod.reply_dispatch_status(
                identity_id=t["identity_id"],
                campaign_id=t["campaign_id"],
                message_id=t["message_id"],
                env=args.env,
            )
            print(
                f"  LIVE:{t['identity_id']}: exhausted={st.get('has_dispatch_exhausted_event')} "
                f"retry={st.get('should_retry_gateway_only')} chase={st.get('chase_action')}"
            )

    if not args.apply:
        print("\nRe-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
