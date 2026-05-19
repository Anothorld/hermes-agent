"""Backfill the Conversation Audit Layer (CAL) from existing Kanban KOL cards.

Idempotent: re-running adds no duplicates. Use this once when migrating an
existing campaign onto the bridge plugin (the skills now write to CAL on every
new action, but pre-existing cards have no CAL history).

Strategy
--------
For each Kanban task whose title starts with ``kol:`` and whose body YAML
carries ``campaign_id`` / ``kol_handle``:

1. ``cal.upsert_identity`` for ``(platform=instagram, handle, env)``
2. Register aliases the card already knows about:
   - ``email`` if present
   - ``gmail_thread_id`` if present
3. Emit a single ``cal.record_event(event_type='backfilled', ...)`` so the
   timeline has a starting anchor. Subsequent events come from live skills.
4. If the card has ``draft_ids.{initial,product_pitch,negotiation}`` set
   without a corresponding CAL row, emit a placeholder ``backfilled_draft``
   event referencing the draft_id; we do NOT fabricate draft bodies — the
   real body lives in Gmail and can be reconciled later.

Usage
-----
    python -m kol_ops_bridge.scripts.backfill --board kol-outreach --env LIVE
    python -m kol_ops_bridge.scripts.backfill --board kol-outreach --dry-run

The script is read-only against Kanban (never modifies cards).
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any, Optional

import yaml  # type: ignore[import-untyped]

log = logging.getLogger("kol_ops_bridge.backfill")

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_pkg"


def _load_cal() -> types.ModuleType:
    """Import cal.py despite the hyphenated plugin dir name."""
    if _PKG in sys.modules:
        return sys.modules[_PKG].cal  # type: ignore[attr-defined]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = pkg
    for sub in ("schema", "cal"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{sub}", _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg.cal  # type: ignore[attr-defined]


def _iter_kol_cards(board: str) -> list[dict[str, Any]]:
    """Yield KOL cards from the named Kanban board.

    Fails soft: if Hermes' kanban_db is not importable (e.g. running the
    script outside the venv), return [] and let the caller report.
    """
    try:
        from hermes_cli import kanban_db as kb  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        log.error("Unable to import hermes_cli.kanban_db: %s", exc)
        return []

    conn = kb.open_kanban_db(board=board)  # type: ignore[attr-defined]
    try:
        tasks = kb.list_tasks(conn, include_archived=True)
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for t in tasks:
        title = (t.title or "").strip()
        if not title.startswith("kol:"):
            continue
        body = _parse_body(t.body or "")
        if not body or "kol_handle" not in body:
            continue
        body["_task_id"] = t.id
        out.append(body)
    return out


def _parse_body(body: str) -> Optional[dict[str, Any]]:
    """Parse a Kanban card body that may be plain YAML or fenced."""
    text = body.strip()
    if text.startswith("```"):
        # Strip first fence + last fence.
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1])
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("YAML parse failed: %s", exc)
        return None
    return parsed if isinstance(parsed, dict) else None


def backfill(*, board: str, env: str, dry_run: bool) -> dict[str, int]:
    cal = _load_cal()
    cards = _iter_kol_cards(board)
    stats = {"cards": 0, "identities": 0, "aliases": 0, "events": 0}

    for body in cards:
        stats["cards"] += 1
        handle = str(body["kol_handle"]).strip()
        email = body.get("email")
        thread = body.get("gmail_thread_id")
        card_id = body.get("_task_id")
        campaign_id = body.get("campaign_id")
        sku = body.get("product_sku")
        stage = body.get("stage") or "discovered"
        sub_status = body.get("sub_status") or body.get("status")

        if dry_run:
            log.info("[dry-run] would backfill handle=%s thread=%s email=%s card=%s",
                     handle, thread, email, card_id)
            continue

        kid = cal.upsert_identity(
            handle=handle,
            platform="instagram",
            primary_email=email,
            creator_type=body.get("creator_type"),
            env=env,
        )
        if kid is None:
            log.warning("upsert_identity returned None for %s; skipping", handle)
            continue
        stats["identities"] += 1

        for kind, value in (("email", email), ("gmail_thread_id", thread), ("handle", handle)):
            if value:
                cal.add_alias(kol_identity_id=kid, kind=kind, value=str(value),
                              source="backfill", env=env)
                stats["aliases"] += 1

        cal.record_event(
            kol_identity_id=kid,
            event_type="backfilled",
            actor="backfill",
            card_id=card_id,
            product_sku=sku,
            campaign_id=campaign_id,
            stage=stage,
            sub_status=sub_status,
            payload={
                "status": body.get("status"),
                "selling_point_group": body.get("selling_point_group"),
                "final_fit": body.get("final_fit"),
            },
            env=env,
        )
        stats["events"] += 1

        for draft_stage, draft_id in (body.get("draft_ids") or {}).items():
            if not draft_id:
                continue
            existing = cal.get_draft(str(draft_id), env=env)
            if existing:
                continue
            cal.record_event(
                kol_identity_id=kid,
                event_type=f"backfilled_draft:{draft_stage}",
                actor="backfill",
                card_id=card_id,
                product_sku=sku,
                campaign_id=campaign_id,
                stage=stage,
                sub_status=sub_status,
                payload={"draft_id": draft_id, "note": "body lives in Gmail; reconcile later"},
                env=env,
            )
            stats["events"] += 1

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill CAL from existing Kanban KOL cards.")
    parser.add_argument("--board", default="kol-outreach", help="Kanban board slug.")
    parser.add_argument("--env", default="LIVE", choices=("LIVE", "TEST"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    stats = backfill(board=args.board, env=args.env, dry_run=args.dry_run)
    log.info("backfill stats: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
