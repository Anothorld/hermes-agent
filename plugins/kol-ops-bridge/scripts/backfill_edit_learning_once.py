#!/usr/bin/env python3
"""One-shot in-process backfill for ``draft_edit_learning`` (operator recovery).

Uses ``~/.hermes/kol-ops-bridge/cal.db`` via CAL — does not open SQLite ad hoc.
Requires Gmail tokens (same as Bridge reconcile). Prefer
``kol_bridge_tool.py backfill-edit-learning`` when the Bridge HTTP API is up.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_backfill_pkg"


def _load_pkg() -> types.ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = pkg
    for sub in (
        "schema",
        "cal",
        "reply_diff",
        "mailbox_resolver",
        "gmail_client",
        "gmail_credentials",
        "gmail_console",
        "gmail_reconcile",
    ):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{sub}", _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


def main() -> int:
    env = (os.environ.get("KOL_BACKFILL_ENV") or "LIVE").strip().upper()
    dry_run = os.environ.get("KOL_BACKFILL_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    limit = int(os.environ.get("KOL_BACKFILL_LIMIT", "500"))
    pkg = _load_pkg()
    gr = pkg.gmail_reconcile
    try:
        result = gr.backfill_edit_learning_all_mailboxes(
            env=env,
            dry_run=dry_run,
            limit=limit,
        )
    except pkg.gmail_client.GmailUnavailable as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
