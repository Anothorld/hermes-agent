"""Weekly distill cron — T3 offline policy distillation, auto-promote (no approve).

Adaptive loop:
1. latest_weekly_trend() compares this week vs last week pass-rate.
   - this_week >= last_week → mode="repair" (incremental edits on baseline)
   - this_week <  last_week → mode="rebuild" (regenerate from cumulative samples)
2. distill_intent_policy_llm() produces a candidate policy.
3. eval_runner scores the candidate against the golden set.
4. Candidate accuracy >= current → auto-promote (bump version, archive old).
   Else → reject, log, next week the trend decides rebuild/repair again.

All decisions audited in cs_learning_job_runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import db, eval_runner, learning

log = logging.getLogger(__name__)
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def run(*, env: str = "LIVE") -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cfg = learning._load_learning_config()
    trend = learning.latest_weekly_trend(env=env, weeks=2)
    mode = "repair" if trend.get("direction") in ("up", "flat") else "rebuild"
    if trend.get("direction") == "insufficient":
        mode = "rebuild"  # not enough data → rebuild from whatever we have

    # gather samples (this week for repair; cumulative for rebuild)
    corrections = learning.list_intent_corrections(env=env, limit=200 if mode == "rebuild" else 50)

    baseline_md = ""
    try:
        baseline_md = (_CONFIG_DIR / "intent_policy.md").read_text(encoding="utf-8")
    except OSError:
        pass

    candidate_md, used_llm = learning.distill_intent_policy_llm(
        env=env, baseline_md=baseline_md, samples=corrections, mode=mode
    )

    # eval candidate: temporarily write it and run eval, then compare
    # NOTE: for a clean comparison we eval the candidate by swapping the policy
    # file briefly. In production this should be done against a temp copy; here
    # we keep it simple and rely on the eval running synchronously.
    policy_path = _CONFIG_DIR / "intent_policy.md"
    old_policy = policy_path.read_text(encoding="utf-8") if policy_path.exists() else ""
    try:
        policy_path.write_text(candidate_md, encoding="utf-8")
        cand_result = eval_runner.run_eval(use_llm=False)
    finally:
        policy_path.write_text(old_policy, encoding="utf-8")

    current_result = eval_runner.run_eval(use_llm=False)
    min_delta = float(cfg.get("promote_min_accuracy_delta", 0.0))
    promoted = False
    if cand_result.accuracy - current_result.accuracy >= min_delta:
        learning.promote_intent_prompt(new_policy_md=candidate_md, eval_snapshot={"accuracy": cand_result.accuracy})
        promoted = True
        status = "promoted"
    else:
        status = "rejected"

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.record_job_run(
        env=env,
        job="intent_optimize_distill",
        started_at=started,
        finished_at=finished,
        status=status,
        payload={
            "mode": mode,
            "trend": trend,
            "candidate_accuracy": cand_result.accuracy,
            "current_accuracy": current_result.accuracy,
            "promoted": promoted,
            "used_llm": used_llm,
            "samples": len(corrections),
        },
    )
    log.info(
        "intent_optimize_distill env=%s mode=%s trend=%s cand=%.3f cur=%.3f promoted=%s",
        env, mode, trend.get("direction"), cand_result.accuracy, current_result.accuracy, promoted,
    )
    return {
        "mode": mode,
        "trend": trend,
        "candidate_accuracy": cand_result.accuracy,
        "current_accuracy": current_result.accuracy,
        "promoted": promoted,
    }


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    run(env=os.environ.get("CS_INTENT_ENV", "LIVE"))
