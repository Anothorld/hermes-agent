#!/usr/bin/env python3
"""daily-git-summary.py — Collect yesterday's git commits (Beijing time).

Output: pipe-separated lines: hash|author|date|message
If no commits, outputs: NO_COMMITS
"""

import subprocess
import sys
from datetime import datetime, timedelta

def get_beijing_date():
    """Get current date in Beijing time (UTC+8)."""
    # Use system date with TZ override
    try:
        result = subprocess.run(
            ["date", "-u", "+%Y-%m-%d %H:%M:%S"],
            capture_output=True, text=True, timeout=5
        )
        utc_now = datetime.strptime(result.stdout.strip(), "%Y-%m-%d %H:%M:%S")
        # Beijing = UTC+8
        beijing_now = utc_now + timedelta(hours=8)
        return beijing_now.date()
    except Exception:
        # Fallback: assume local time is close enough
        return datetime.now().date()

def main():
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    
    today = get_beijing_date()
    yesterday = today - timedelta(days=1)
    
    since = f"{yesterday} 00:00:00"
    until = f"{today} 00:00:00"
    
    result = subprocess.run(
        ["git", "-C", repo_dir, "log", "--all",
         f"--since={since}", f"--until={until}",
         "--format=%H|%an|%ai|%s"],
        capture_output=True, text=True, timeout=30
    )
    
    output = result.stdout.strip()
    if not output:
        print("NO_COMMITS")
    else:
        print(output)
    
    # Also print diff stats
    stat_result = subprocess.run(
        ["git", "-C", repo_dir, "log", "--all",
         f"--since={since}", f"--until={until}",
         "--stat", "--format="],
        capture_output=True, text=True, timeout=30
    )
    
    if stat_result.stdout.strip():
        print("---STATS---")
        print(stat_result.stdout.strip())

if __name__ == "__main__":
    main()
