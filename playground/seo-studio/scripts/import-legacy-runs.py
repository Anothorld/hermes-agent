#!/usr/bin/env python3
"""One-shot import of legacy run directories into SEO Studio tasks/steps."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        default=os.environ.get(
            "SEO_RUNS_DIR",
            str(Path.home() / ".hermes/skills/productivity/povison-seo-blog/runs"),
        ),
        help="Directory containing run-* folders",
    )
    parser.add_argument("--run-id", help="Import a single run folder by name")
    parser.add_argument("--force", action="store_true", help="Re-import even if task exists")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir).resolve()
    if args.run_id:
        result = db.import_run_from_disk(
            runs_dir / args.run_id,
            skip_existing=not args.force,
        )
    else:
        result = db.import_runs_from_disk(
            runs_dir,
            skip_existing=not args.force,
            limit=args.limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
