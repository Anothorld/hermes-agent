"""Ops CLI for cs-intent-classifier.

Usage::

    python plugins/cs-intent-classifier/scripts/cs_intent_cli.py classify \
        --session-id 123 --subject "..." --body "..."
    python plugins/cs-intent-classifier/scripts/cs_intent_cli.py get \
        --session-id 123
    python plugins/cs-intent-classifier/scripts/cs_intent_cli.py eval
    python plugins/cs-intent-classifier/scripts/cs_intent_cli.py promote v2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from cs_intent_classifier_pkg import classifier, db  # type: ignore[attr-defined]


def _cmd_classify(args: argparse.Namespace) -> None:
    metadata = {}
    if args.metadata:
        metadata = json.loads(args.metadata)
    ge = classifier.classify(subject=args.subject, body=args.body, metadata=metadata)
    ts = db.insert_classification(
        session_id=args.session_id,
        env=args.env,
        gate_extract=ge,
        model_version=ge.get("model_version", "v1"),
        classifier_source=ge.get("classifier_source", "keyword"),
    )
    print(json.dumps({"classified_at": ts, "gate_extract": ge}, ensure_ascii=False, indent=2))


def _cmd_get(args: argparse.Namespace) -> None:
    row = db.latest_classification(session_id=args.session_id, env=args.env)
    if not row:
        print("not classified", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(row, ensure_ascii=False, indent=2))


def _cmd_eval(args: argparse.Namespace) -> None:
    # Phase 4 will wire this to eval_runner; stub for now.
    print("eval not yet implemented (Phase 4)")


def _cmd_promote(args: argparse.Namespace) -> None:
    version_path = _PLUGIN_ROOT / "config" / "intent_version.txt"
    version_path.write_text(args.version + "\n", encoding="utf-8")
    print(f"promoted model_version → {args.version}")


def main() -> None:
    p = argparse.ArgumentParser(description="cs-intent-classifier ops CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("classify")
    c.add_argument("--session-id", required=True)
    c.add_argument("--env", default="LIVE")
    c.add_argument("--subject", default="")
    c.add_argument("--body", default="")
    c.add_argument("--metadata", default="")
    c.set_defaults(func=_cmd_classify)

    g = sub.add_parser("get")
    g.add_argument("--session-id", required=True)
    g.add_argument("--env", default="LIVE")
    g.set_defaults(func=_cmd_get)

    e = sub.add_parser("eval")
    e.set_defaults(func=_cmd_eval)

    pr = sub.add_parser("promote")
    pr.add_argument("version")
    pr.set_defaults(func=_cmd_promote)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
