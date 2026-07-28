#!/usr/bin/env python3
"""Review unknown attributes in the Knowledge bank against the reference vocabulary.

Plan P0.5 requires: 解析时优先映射已知属性名，未知原样透传并标记，定期人工 review 并入。
This script is the "定期人工 review 并入" half — it enumerates distinct attribute
values actually stored in the Knowledge bank, compares them against
`hindsight_attribute_vocab.json`, and prints candidates worth promoting into the
canonical vocabulary (high frequency, semantically stable).

Usage:
    python3 review_unknown_attributes.py                 # scan default bank
    python3 review_unknown_attributes.py --limit 500
    python3 review_unknown_attributes.py --apply-draft     # emit a draft vocab patch to stdout

It does NOT mutate the bank or the vocab file; `--apply-draft` only prints a JSON
snippet you can paste into hindsight_attribute_vocab.json after manual approval.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_ROOT)
import hindsight_ko as ko  # noqa: E402

API_PREFIX = "/v1/default"
LIST_TIMEOUT = 30


def _list_memories(*, limit: int, offset: int) -> dict[str, Any]:
    import httpx
    url = f"{ko.HINDSIGHT_BASE_URL}{API_PREFIX}/banks/{ko.KNOWLEDGE_BANK}/memories/list"
    with httpx.Client(timeout=LIST_TIMEOUT, headers=ko._headers()) as c:
        r = c.get(url, params={"limit": limit, "offset": offset})
        r.raise_for_status()
        return r.json()


def _collect_attributes(limit: int) -> tuple[Counter, Counter]:
    """Page through memories and tally attribute values (known vs unknown)."""
    vocab = ko._load_vocab()
    known = {a["attribute"] for a in vocab.get("attributes", []) if isinstance(a, dict)}
    known_synonyms = set()
    for a in vocab.get("attributes", []):
        if isinstance(a, dict):
            for s in a.get("synonyms", []):
                known_synonyms.add(s.lower().replace(" ", "_"))

    attr_counter: Counter[str] = Counter()
    offset = 0
    seen = 0
    while seen < limit:
        page_size = min(200, limit - seen)
        page = _list_memories(limit=page_size, offset=offset)
        items = page.get("items", [])
        if not items:
            break
        for it in items:
            meta = it.get("metadata") or {}
            attr = (meta.get("attribute") or "").strip()
            if attr:
                attr_counter[attr] += 1
        seen += len(items)
        offset += page_size
        if len(items) < page_size:
            break
    known_hits = Counter({a: n for a, n in attr_counter.items()
                          if a in known or a.lower().replace(" ", "_") in known_synonyms})
    unknown_hits = Counter({a: n for a, n in attr_counter.items()
                            if a not in known and a.lower().replace(" ", "_") not in known_synonyms})
    return known_hits, unknown_hits


def _suggest_canonical(attr: str) -> str:
    """Heuristic canonical-name suggestion for an unknown attribute."""
    return attr.strip().lower().replace(" ", "_").replace("-", "_")


def main() -> None:
    ap = argparse.ArgumentParser(description="Review unknown attributes vs the reference vocab.")
    ap.add_argument("--limit", type=int, default=1000, help="max memories to scan (default 1000)")
    ap.add_argument("--apply-draft", action="store_true",
                    help="print a JSON snippet of unknown-attribute candidates to paste into the vocab")
    args = ap.parse_args()

    print(f"Scanning {ko.KNOWLEDGE_BANK} @ {ko.HINDSIGHT_BASE_URL} (limit={args.limit})...\n")
    known, unknown = _collect_attributes(args.limit)

    print(f"=== Known attributes (already in vocab): {len(known)} distinct ===")
    for a, n in known.most_common():
        print(f"  {n:4d}  {a}")
    print(f"\n=== Unknown attributes (candidates for review): {len(unknown)} distinct ===")
    for a, n in unknown.most_common():
        print(f"  {n:4d}  {a}")

    if args.apply_draft and unknown:
        draft = [
            {
                "attribute": _suggest_canonical(a),
                "category": "review",
                "synonyms": [a],
            }
            for a, _n in unknown.most_common()
        ]
        print("\n=== Draft vocab snippet (paste into hindsight_attribute_vocab.json after review) ===")
        print(json.dumps(draft, ensure_ascii=False, indent=2))

    print("\nManual step: review the unknown list above, merge genuine canonical attributes")
    print("into hindsight_attribute_vocab.json, and re-run to confirm coverage improves.")


if __name__ == "__main__":
    main()
