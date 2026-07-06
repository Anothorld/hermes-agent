"""Daily eval cron — records pass-rate snapshot, observes only (no policy change).

Runs the golden-set eval against the current model_version and writes a row to
cs_intent_eval_daily. The weekly distill cron reads this trend to decide
rebuild vs repair.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import db, eval_runner, learning

log = logging.getLogger(__name__)


def run(*, env: str = "LIVE") -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = eval_runner.run_eval(use_llm=False)
    date = datetime.now(timezone.utc).date().isoformat()
    model_version = learning.current_model_version()
    per_intent = {
        intent: {"accuracy": (b["correct"] / b["total"]) if b["total"] else 0.0, "total": b["total"]}
        for intent, b in result.per_intent.items()
    }
    db.record_eval_snapshot(
        date=date,
        env=env,
        model_version=model_version,
        accuracy=result.accuracy,
        per_intent=per_intent,
        by_source={"fabrication_failures": result.fabrication_failures},
    )
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.record_job_run(
        env=env,
        job="intent_eval_daily",
        started_at=started,
        finished_at=finished,
        status="ok",
        payload={"accuracy": result.accuracy, "total": result.total, "fabrication_failures": result.fabrication_failures, "model_version": model_version},
    )
    log.info("intent_eval_daily env=%s accuracy=%.3f total=%d", env, result.accuracy, result.total)
    return {"accuracy": result.accuracy, "total": result.total, "fabrication_failures": result.fabrication_failures}


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run(env=os.environ.get("CS_INTENT_ENV", "LIVE"))
