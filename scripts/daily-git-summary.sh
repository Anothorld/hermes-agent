#!/usr/bin/env bash
# daily-git-summary.sh — Collect previous day's git commits (raw data for agent summarization)
# Output: raw commit data + stats to stdout
# Called by Hermes cron job, then agent generates Chinese summary

set -euo pipefail

REPO_DIR="/Users/arnold/agent_prj/hermes-agent"

# Calculate yesterday's date in Asia/Shanghai
YESTERDAY=$(TZ=Asia/Shanghai date -v-1d +%Y-%m-%d 2>/dev/null || TZ=Asia/Shanghai date -d "yesterday" +%Y-%m-%d)
TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)

cd "$REPO_DIR"

# Get commits from yesterday (Beijing time)
COMMITS=$(TZ=Asia/Shanghai git log --format="%h|%an|%ad|%s" --date=format:"%Y-%m-%d %H:%M" \
  --after="${YESTERDAY} 00:00" --before="${TODAY} 00:00" --all 2>/dev/null || true)

if [ -z "$COMMITS" ]; then
    echo "DATE=${YESTERDAY}"
    echo "COMMITS=0"
    exit 0
fi

COMMIT_COUNT=$(echo "$COMMITS" | wc -l | tr -d ' ')

echo "DATE=${YESTERDAY}"
echo "COMMITS=${COMMIT_COUNT}"
echo "---COMMITS---"
echo "$COMMITS"
echo "---STATS---"

# Aggregate stats
TZ=Asia/Shanghai git log --oneline --shortstat --after="${YESTERDAY} 00:00" --before="${TODAY} 00:00" --all 2>/dev/null | grep -E "file changed|files changed" | awk '{files+=$1; inserted+=$4; deleted+=$6} END {print files " files changed, " inserted " insertions(+), " deleted " deletions(-)"}'

echo "---CHANGED_FILES---"
# Get the diff stat between first and last commit of the day
FIRST_HASH=$(TZ=Asia/Shanghai git log --reverse --format="%H" --after="${YESTERDAY} 00:00" --before="${TODAY} 00:00" --all | head -1)
LAST_HASH=$(TZ=Asia/Shanghai git log --format="%H" --after="${YESTERDAY} 00:00" --before="${TODAY} 00:00" --all | head -1)
if [ -n "$FIRST_HASH" ] && [ -n "$LAST_HASH" ]; then
    TZ=Asia/Shanghai git diff --stat "${FIRST_HASH}^..${LAST_HASH}" 2>/dev/null || echo "(无法获取变更文件列表)"
fi

echo "---DIFF_DETAILS---"
# Get detailed diff for key files (truncated)
TZ=Asia/Shanghai git diff "${FIRST_HASH}^..${LAST_HASH}" --stat-width=120 2>/dev/null | tail -5 || true
