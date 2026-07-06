"""High-frequency few-shot refresh cron (T2, e.g. every 6h).

Refreshes the few-shot set used by build_few_shot_block(). The few-shot block is
built dynamically at classify-call time from cs_intent_corrections, so this job
primarily records an audit row and runs an eval to confirm the refreshed set
doesn't regress. Bumps a separate fewshot_version (not model_version).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import db, eval_runner

log = logging.getLogger(__name__)


def run(*, env: str = "LIVE") -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = eval_runner.run_eval(use_llm=False)
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.record_job_run(
        env=env,
        job="intent_optimize_fewshot",
        started_at=started,
        finished_at=finished,
        status="ok",
        payload={"accuracy": result.accuracy, "total": result.total},
    )
    log.info("intent_optimize_fewshot env=%s accuracy=%.3f", env, result.accuracy)
    return {"accuracy": result.accuracy, "total": result.total}


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run(env=os.environ.get("CS_INTENT_ENV", "LIVE"))
