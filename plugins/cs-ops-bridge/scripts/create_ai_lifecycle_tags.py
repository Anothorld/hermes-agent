#!/usr/bin/env python3
"""Create AI lifecycle tag group + children in QuickCEP (idempotent)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
from profile_refs import quickcep_skill_dir  # noqa: E402

PARENT_NAME = "AI客服"
CHILDREN = [
    "AI-处理中",
    "AI-草稿待审",
    "AI-待专家",
    "AI-处理失败",
    "AI-已结案",
]


def _cli() -> Path:
    skill = Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(quickcep_skill_dir())))
    return skill / "scripts" / "quickcep_cli.py"


def _fetch_tree() -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(_cli()), "tags-tree"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout)
    data = json.loads(proc.stdout)
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("tags") or []


def _flatten(groups: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(node: dict) -> None:
        name = str(node.get("name") or "").strip()
        tid = node.get("id") or node.get("tagId")
        if name and tid:
            out[name] = str(tid)
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    for group in groups:
        walk(group)
    return out


def _api_post(body: dict) -> dict:
    code = f"""
import json, sys
sys.path.insert(0, {json.dumps(str(_cli().parent))})
from quickcep_cli import get_jwt, api_request
import argparse
args = argparse.Namespace(token=None, email=None, password=None)
jwt = get_jwt(args)
result = api_request("POST", "/store/sessionTag", jwt, body={json.dumps(body)})
print(json.dumps(result))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def _create_tag(name: str, parent_id: str, level: int, dry_run: bool) -> str | None:
    body = {"name": name, "parentId": parent_id, "level": level, "status": 1}
    if dry_run:
        print(f"DRY-RUN create {name!r} parent={parent_id} level={level}")
        return None
    result = _api_post(body)
    if result.get("code") != 200:
        raise SystemExit(f"Failed to create tag {name!r}: {json.dumps(result, ensure_ascii=False)[:500]}")
    data = result.get("data") or {}
    tid = str(data.get("id") or data.get("tagId") or "")
    print(f"Created {name} -> {tid}")
    return tid or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create AI lifecycle tags in QuickCEP")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    flat = _flatten(_fetch_tree())
    parent_id = flat.get(PARENT_NAME)
    if parent_id:
        print(f"Parent exists: {PARENT_NAME} -> {parent_id}")
    else:
        parent_id = _create_tag(PARENT_NAME, "0", 0, args.dry_run)
        if not args.dry_run:
            flat = _flatten(_fetch_tree())
            parent_id = flat.get(PARENT_NAME)
            if not parent_id:
                raise SystemExit(f"Parent tag {PARENT_NAME!r} not found after create")

    for child in CHILDREN:
        if flat.get(child):
            print(f"Child exists: {child} -> {flat[child]}")
            continue
        if not parent_id:
            print(f"SKIP child {child} (dry-run, no parent id)")
            continue
        _create_tag(child, parent_id, 1, args.dry_run)

    print("Done. Run sync_session_tags.py next.")


if __name__ == "__main__":
    main()
