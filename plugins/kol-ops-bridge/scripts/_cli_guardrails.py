"""CLI guardrails for ``kol_bridge_tool`` — catch common agent/operator mistakes.

Preflight runs before argparse so mistakes surface as structured JSON on stderr
plus a plain-language hint (operators) instead of a bare argparse usage dump.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from bridge_agent_contract import (  # noqa: E402
    AGENT_BRIDGE_CONTRACT_LINES,
    CANONICAL_CLI_REL,
    NOX_TOOL_REL,
    lint_agent_bridge_snippet,
)

CLI_EPILOG = f"""
Canonical path (run from hermes-agent/):
  python {CANONICAL_CLI_REL} <subcommand> [options]

Examples:
  python {CANONICAL_CLI_REL} health
  python {CANONICAL_CLI_REL} get-escalation --escalation-id 108
  python {CANONICAL_CLI_REL} list-escalations --env LIVE

This CLI calls the Bridge HTTP API (serve.py). It does not open cal.db directly.
Mutating subcommands require --env TEST or LIVE.

Agent runs: never curl/execute_code the bridge; see print-agent-contract.
""".strip()


def _first_positional(argv: list[str]) -> Optional[str]:
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in ("-h", "--help"):
            return None
        if tok.startswith("-"):
            if tok in ("--bridge-key", "--base", "--env", "--json"):
                skip_next = True
            continue
        return tok
    return None


def _die_cli(error: str, *, hint: str, **fields: object) -> None:
    payload: dict[str, object] = {"error": error, "hint": hint, **fields}
    payload.setdefault("canonical_cli", CANONICAL_CLI_REL)
    line = json.dumps(payload, ensure_ascii=False)
    # Agents only see terminal stdout — emit there so the hint is never lost.
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    sys.stderr.write(line + "\n")
    raise SystemExit(2)


def preflight_argv(argv: list[str]) -> None:
    """Reject known-bad argv combinations before HTTP calls."""
    if not argv or argv in (["-h"], ["--help"]):
        return

    cmd = _first_positional(argv)
    if cmd is None:
        return

    if cmd == "get-escalation" and "--campaign-id" in argv:
        _die_cli(
            "invalid_cli_args",
            hint=(
                "get-escalation only needs --escalation-id (plus optional "
                "--bridge-key). Campaign is on the returned row. "
                "To browse by campaign, use: list-escalations --env LIVE "
                "(then filter by campaign_id in the JSON)."
            ),
            subcommand=cmd,
            rejected_flag="--campaign-id",
        )

    if cmd == "get-campaign" and "--escalation-id" in argv:
        _die_cli(
            "invalid_cli_args",
            hint=(
                "get-campaign uses --campaign-id, not --escalation-id. "
                "For one escalation row use: get-escalation --escalation-id <id>."
            ),
            subcommand=cmd,
            rejected_flag="--escalation-id",
        )

    if "--id" in argv and "--identity-id" not in argv:
        _die_cli(
            "invalid_cli_args",
            hint=(
                "Use --identity-id <integer CAL id>, not --id. "
                f"Example: python {CANONICAL_CLI_REL} get-identity "
                "--identity-id 806 --env LIVE"
            ),
            rejected_flag="--id",
        )

    joined = " ".join(argv)
    if "kol-ops-bridge/scripts/nox_kol" in joined:
        _die_cli(
            "invalid_cli_args",
            hint=(
                f"Nox CLI lives under nox-kol-bridge, not kol-ops-bridge. "
                f"Use: python {NOX_TOOL_REL} contacts --help"
            ),
            rejected_path="kol-ops-bridge/scripts/nox_kol_tool.py",
            canonical_nox_cli=NOX_TOOL_REL,
        )

    if cmd == "write-event":
        has_id_flag = "--identity-id" in argv
        has_json = "--json" in argv
        inline_json = False
        if has_json:
            try:
                jidx = argv.index("--json")
                if jidx + 1 < len(argv) and not argv[jidx + 1].startswith("@"):
                    inline_json = True
            except ValueError:
                pass
        if not has_id_flag and inline_json:
            _die_cli(
                "invalid_cli_args",
                hint=(
                    "write-event: prefer --identity-id, --event-type, --actor flags "
                    "plus `--json @/tmp/event.json` for payload. "
                    "Inline `--json '{...}' often yields empty terminal output."
                ),
                subcommand=cmd,
            )
        if not has_id_flag and not has_json:
            _die_cli(
                "invalid_cli_args",
                hint=(
                    "write-event requires --identity-id, --event-type, --actor "
                    "(or full body via --json @/tmp/event.json)."
                ),
                subcommand=cmd,
            )


def hint_for_argparse_error(message: str, argv: list[str]) -> Optional[str]:
    """Map argparse error text to an operator-facing next step."""
    cmd = _first_positional(argv)

    if "required: cmd" in message or "the following arguments are required: cmd" in message:
        return (
            f"Pick a subcommand, e.g. health or get-escalation. "
            f"Full list: python {CANONICAL_CLI_REL} --help"
        )

    if "unrecognized arguments" in message and cmd == "get-escalation":
        if "--campaign-id" in message or "--campaign-id" in argv:
            return (
                "Drop --campaign-id. Use get-escalation --escalation-id <id> only, "
                "or list-escalations --env LIVE to scan by campaign."
            )

    if "invalid choice" in message and cmd is None:
        return (
            f"Unknown subcommand. Run: python {CANONICAL_CLI_REL} --help"
        )

    if "the following arguments are required: --identity-id" in message:
        return (
            f"Add --identity-id <integer> from dispatch brief. "
            f"Example: python {CANONICAL_CLI_REL} get-identity "
            "--identity-id 806 --env LIVE"
        )

    if "the following arguments are required: --campaign-id" in message:
        return (
            f"Add --campaign-id from brief (e.g. SEB8010-20260608). "
            f"Example: python {CANONICAL_CLI_REL} get-dispatch-context "
            "--identity-id <id> --campaign-id <cid> --env LIVE --view agent"
        )

    if "unrecognized arguments" in message and "--identity-id" in message:
        return (
            "Pass --identity-id as a flag, not a positional arg. "
            f"Example: python {CANONICAL_CLI_REL} write-facts-multi "
            "--identity-id 806 --env LIVE --json @/tmp/facts.json"
        )

    return None


class KolBridgeToolParser(argparse.ArgumentParser):
    """ArgumentParser that attaches guardrail hints to usage errors."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        kwargs.setdefault("epilog", CLI_EPILOG)
        super().__init__(*args, **kwargs)
        self._guard_argv: list[str] = []

    def parse_args(  # type: ignore[override]
        self,
        args: Optional[list[str]] = None,
        namespace: Optional[argparse.Namespace] = None,
    ) -> argparse.Namespace:
        self._guard_argv = list(args) if args is not None else sys.argv[1:]
        preflight_argv(self._guard_argv)
        return super().parse_args(args, namespace)

    def error(self, message: str) -> None:  # noqa: D102 — argparse API
        hint = hint_for_argparse_error(message, self._guard_argv)
        # Emit a machine-readable error on stdout first: argparse's own usage
        # dump goes to stderr, which the agent's terminal tool does not show.
        # Without this, a malformed subcommand looked like empty output and the
        # agent abandoned the CLI for ad-hoc execute_code (POVISON recovery).
        payload: dict[str, object] = {
            "error": "invalid_cli_args",
            "detail": message,
            "canonical_cli": CANONICAL_CLI_REL,
        }
        if hint:
            payload["hint"] = hint
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if hint:
            sys.stderr.write(f"\nHint: {hint}\n")
        super().error(message)
