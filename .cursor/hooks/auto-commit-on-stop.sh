#!/bin/bash

set -euo pipefail

# Consume hook input JSON from stdin (unused for now).
cat >/dev/null

if ! command -v git >/dev/null 2>&1; then
  exit 0
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi

if [[ -z "$(git status --porcelain)" ]]; then
  exit 0
fi

git add -A

if git diff --cached --quiet; then
  exit 0
fi

commit_msg="chore(auto): checkpoint commit on agent stop"
git commit -m "$commit_msg" >/dev/null 2>&1 || true

exit 0
