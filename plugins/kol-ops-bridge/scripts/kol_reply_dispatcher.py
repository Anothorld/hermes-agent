#!/usr/bin/env python3
"""CLI entry for Gmail inbound-reply polling (one-shot or --watch).

Core logic lives in ``inbound_reply``; this script uses HTTP bridge access
for standalone debugging outside ``serve.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_PKG = "kol_ops_bridge_pkg"
if _PKG not in sys.modules:
    import types

    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = _pkg

from kol_ops_bridge_pkg.gmail_client import GmailUnavailable  # noqa: E402
from kol_ops_bridge_pkg.inbound_reply import run_once  # noqa: E402
from kol_ops_bridge_pkg.inbound_reply.deps import InboundDeps  # noqa: E402

log = logging.getLogger("kol_reply_dispatcher")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["TEST", "LIVE"], required=True)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--watch", action="store_true", help="poll forever instead of one-shot")
    parser.add_argument("--interval", type=int, default=60, help="seconds between polls when --watch")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    deps = InboundDeps.http_default()

    def _tick() -> None:
        try:
            stats = run_once(
                env=args.env,
                lookback_days=args.lookback_days,
                max_results=args.max_results,
                deps=deps,
            )
        except GmailUnavailable as exc:
            log.error("gmail unavailable: %s", exc)
            return
        log.info("tick env=%s stats=%s", args.env, json.dumps(stats))

    _tick()
    while args.watch:
        time.sleep(max(5, args.interval))
        _tick()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
