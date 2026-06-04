"""Meta / lint subcommands for ``kol_bridge_tool`` (agent contract checks)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _cli_guardrails import (  # noqa: E402
    AGENT_BRIDGE_CONTRACT_LINES,
    CANONICAL_CLI_REL,
    lint_agent_bridge_snippet,
)


def cmd_lint_agent_code(args: argparse.Namespace) -> None:
    """Print JSON lint result for a code snippet (CI / operator spot-check)."""
    text = args.snippet or ""
    if args.snippet_file:
        text = Path(args.snippet_file).read_text(encoding="utf-8", errors="replace")
    violations = lint_agent_bridge_snippet(text)
    out = {
        "ok": not violations,
        "violations": violations,
        "canonical_cli": CANONICAL_CLI_REL,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if violations and args.strict:
        raise SystemExit(2)


def cmd_print_agent_contract(_args: argparse.Namespace) -> None:
    """Emit the bridge agent contract (for SKILL / console prompt embedding)."""
    print("\n".join(AGENT_BRIDGE_CONTRACT_LINES))


def register(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "lint-agent-code",
        help="Lint a code snippet for forbidden bridge bypass patterns (--strict exits 2).",
    )
    p.add_argument("--snippet", default=None, help="Inline Python/shell snippet.")
    p.add_argument("--snippet-file", default=None, help="Path to snippet file.")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when any violation is found.",
    )
    p.set_defaults(func=cmd_lint_agent_code)

    p = sub.add_parser(
        "print-agent-contract",
        help="Print mandatory agent bridge rules (no HTTP).",
    )
    p.set_defaults(func=cmd_print_agent_contract)
