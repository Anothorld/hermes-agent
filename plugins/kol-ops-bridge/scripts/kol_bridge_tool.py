#!/usr/bin/env python3
"""Deterministic CLI wrapper for the ``kol-ops-bridge`` HTTP API.

SKILL / agent-facing tool.  **Never** opens the CAL SQLite directly;
every subcommand is a thin shell over the plugin HTTP endpoints exposed
by ``serve.py`` (default ``http://127.0.0.1:8080``).  Designed so that
deterministic CRUD-like operations (writing facts, opening escalations,
selecting candidates, archiving collabs, persisting policy docs, ...)
can be invoked from a SKILL.md ``Procedure`` section without letting an
LLM hand-roll SQL or curl.

Auth: pass ``--bridge-key``; otherwise the CLI reads
``HERMES_KOL_OPS_BRIDGE_KEY`` / console aliases or
``~/.hermes/kol-ops-bridge/secrets.yaml``.  As a dev-console fallback it
also reads ``playground/kol-ops-console/.env`` from the source tree.
Open mode (no key) is dev-only — the plugin refuses mutating endpoints
in that case.

All env-aware mutating subcommands require an explicit ``--env``
(``TEST`` or ``LIVE``); we never default it to prevent cross-env writes.

Subcommands are registered by domain modules:
- ``_subcmd_campaigns``  — campaign + candidate operations
- ``_subcmd_identities`` — identity + event + timeline
- ``_subcmd_facts``      — facts read/write
- ``_subcmd_governance`` — escalations + approvals + policies

Run ``kol_bridge_tool.py --help`` for the full subcommand list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

# Make ``python /abs/path/kol_bridge_tool.py`` work as well as
# ``python -m hermes-agent.plugins.kol-ops-bridge.scripts.kol_bridge_tool``
# by adding this directory to ``sys.path`` before pulling in siblings.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _subcmd_campaigns  # noqa: E402
import _subcmd_facts  # noqa: E402
import _subcmd_governance  # noqa: E402
import _subcmd_identities  # noqa: E402
from _cal_client import add_common_args, client_from_args, print_json  # noqa: E402


def _cmd_health(args: argparse.Namespace) -> None:
    print_json(client_from_args(args).request("GET", "/health"))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kol_bridge_tool",
        description="Deterministic CLI for hermes kol-ops-bridge.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("health", help="GET /health — bridge healthcheck.")
    add_common_args(h)
    h.set_defaults(func=_cmd_health)

    _subcmd_campaigns.register(sub)
    _subcmd_identities.register(sub)
    _subcmd_facts.register(sub)
    _subcmd_governance.register(sub)
    return p


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover — defensive
        sys.stderr.write(json.dumps({"error": "unexpected", "detail": str(exc)}))
        sys.stderr.write("\n")
        raise
