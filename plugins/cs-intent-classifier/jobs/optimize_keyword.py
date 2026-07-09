"""Keyword optimize cron — scheme 1 automatic failure bank + overlay self-eval.

Runs:
1. Sync keyword false-positive corrections → failures.jsonl
2. Propose fallthrough overlays from recurring FP phrases
3. Self-eval against golden + failure bank (must not regress golden; must
   reduce failure-bank FP rate)
4. Auto-promote overlays only when the gate passes; otherwise reject + audit

Schedule suggestion: daily after ``eval_daily``, or every 6h with fewshot.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Plugin root on sys.path so ``jobs.bootstrap`` resolves when run as
# ``python3 jobs/optimize_keyword.py`` (script dir is jobs/, not root).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from jobs.bootstrap import db, keyword_learning

log = logging.getLogger(__name__)


def run(*, env: str = "LIVE") -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = keyword_learning.run_keyword_optimize_cycle(env=env)
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = str(result.get("status") or "ok")
    db.record_job_run(
        env=env,
        job="intent_optimize_keyword",
        started_at=started,
        finished_at=finished,
        status=status,
        payload=result,
    )
    log.info(
        "intent_optimize_keyword env=%s status=%s promoted=%s",
        env,
        status,
        result.get("promoted"),
    )
    return result


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run(env=os.environ.get("CS_INTENT_ENV", "LIVE"))
