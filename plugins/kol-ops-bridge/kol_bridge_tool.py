#!/usr/bin/env python3
"""Compatibility entry for ``kol_bridge_tool``.

Some agents invoke ``plugins/kol-ops-bridge/kol_bridge_tool.py`` (missing
``scripts/``). This shim forwards to the real CLI so the command still works,
while stderr reminds callers of the canonical path.

Set ``KOL_BRIDGE_TOOL_QUIET_SHIM=1`` to suppress the one-line redirect notice.
"""

from __future__ import annotations

import os
import runpy
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_TARGET = os.path.join(_ROOT, "scripts", "kol_bridge_tool.py")


def main() -> None:
    if not os.path.isfile(_TARGET):
        sys.stderr.write(
            '{"error":"cli_not_found","hint":"Expected scripts/kol_bridge_tool.py '
            'next to this shim.","canonical_cli":"plugins/kol-ops-bridge/scripts/kol_bridge_tool.py"}\n',
        )
        raise SystemExit(2)

    if os.environ.get("KOL_BRIDGE_TOOL_QUIET_SHIM") != "1":
        sys.stderr.write(
            "kol_bridge_tool: compatibility shim — prefer "
            "plugins/kol-ops-bridge/scripts/kol_bridge_tool.py\n",
        )

    sys.argv[0] = _TARGET
    runpy.run_path(_TARGET, run_name="__main__")


if __name__ == "__main__":
    main()
