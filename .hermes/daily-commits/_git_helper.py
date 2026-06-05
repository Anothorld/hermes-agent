#!/usr/bin/env python3
"""Helper to write git log to a known file."""
import subprocess, os, sys
from datetime import datetime, timedelta, timezone

repo = sys.argv[1] if len(sys.argv) > 1 else "/Users/arnold/agent_prj/hermes-agent"
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(repo, ".hermes", "daily-commits", "_raw.txt")

shanghai_tz = timezone(timedelta(hours=8))
now_shanghai = datetime.now(shanghai_tz)
yesterday = now_shanghai - timedelta(days=1)
since = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S +08:00")
until = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999).strftime("%Y-%m-%d %H:%M:%S +08:00")
date_str = yesterday.strftime("%Y-%m-%d")

result = subprocess.run(
    ["git", "log", f"--since={since}", f"--until={until}", "--format=%H|%h|%an|%ai|%s", "--stat"],
    capture_output=True, text=True, timeout=30, cwd=repo
)

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as f:
    if not result.stdout.strip():
        f.write(f"NO_COMMITS:{date_str}\n")
    else:
        f.write(f"DATE:{date_str}\n")
        f.write(result.stdout)

print(f"Written to {out}")
