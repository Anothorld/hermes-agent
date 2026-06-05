#!/usr/bin/env python3
"""Import 曾触达列表.xlsx into data/prior_touch_allowlist.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from prior_touch_allowlist import write_allowlist_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "xlsx",
        nargs="?",
        default=str(Path.home() / "Documents" / "曾触达列表.xlsx"),
        help="Source workbook (default: ~/Documents/曾触达列表.xlsx)",
    )
    parser.add_argument(
        "--out",
        default=str(_PLUGIN_ROOT / "data" / "prior_touch_allowlist.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()
    xlsx = Path(args.xlsx).expanduser()
    if not xlsx.exists():
        print(f"file not found: {xlsx}", file=sys.stderr)
        return 1
    out = write_allowlist_json(xlsx_path=xlsx, out_path=Path(args.out))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
