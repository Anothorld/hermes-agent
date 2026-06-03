#!/bin/bash
# daily-git-summary.sh — Collect yesterday's git commits (Beijing time)
# Output: pipe-separated lines: hash|author|date|message

set -euo pipefail

# Beijing time (UTC+8)
TZ_OFFSET="Asia/Shanghai"

# Calculate yesterday in Beijing time
if date --version &>/dev/null 2>&1; then
    # GNU date (Linux)
    YESTERDAY=$(TZ="$TZ_OFFSET" date -d 'yesterday' '+%Y-%m-%d')
    SINCE="${YESTERDAY} 00:00:00"
    UNTIL="${YESTERDAY} 23:59:59"
else
    # BSD date (macOS)
    YESTERDAY=$(TZ="$TZ_OFFSET" date -v-1d '+%Y-%m-%d')
    SINCE="${YESTERDAY} 00:00:00"
    UNTIL="${YESTERDAY} 23:59:59"
fi

# Try to find a git repo — prefer workdir, fall back to the project
REPO_DIR="${1:-.}"

if [ ! -d "$REPO_DIR/.git" ]; then
    REPO_DIR="/Users/arnold/agent_prj/hermes-agent"
fi

# Collect commits
COMMITS=$(git -C "$REPO_DIR" log --all \
    --since="$SINCE" \
    --until="$UNTIL" \
    --format="%H|%an|%ai|%s" 2>/dev/null || true)

if [ -z "$COMMITS" ]; then
    echo "NO_COMMITS"
else
    echo "$COMMITS"
fi
