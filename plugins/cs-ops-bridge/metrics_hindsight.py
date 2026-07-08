"""HindSight recall → de-escalation metric seam for the 数据页签.

Reads the agent-side HindSight recall audit log and aggregates, per Beijing
natural day, the "HindSight 减升率":

    减升率 = DISTINCT(hit AND auto_handled) sessions
             / DISTINCT(escalated OR (hit AND auto_handled)) sessions

  分子 = 因 HindSight 召回命中并被采用、从而未升级的工单
  分母 = "HindSight 决策域":要么升级了(outcome=escalated),要么靠 HindSight
         命中免升级(hit+auto_handled)。不含 miss+auto_handled(非 HindSight 功劳)
         与从未走过 recall 检查的 auto_handle 主路径工单。

Source: ``hindsight_recall_log.jsonl`` — written by the orchestrator flow's
``hindsight_recall_tracker.py`` (agent-side soft埋点). Path resolves via
``CS_OPS_HINDSIGHT_RECALL_LOG`` env, else ``{CS_OPS_PROFILE_DIR}/data/hindsight_recall_log.jsonl``.

Note: tracker is a soft埋点 (agent自觉记录), 漏记会使分子分母同时偏低;
见 docs/features/metrics/GUIDE.md 的 pitfalls。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_BJ_TZ = timezone(timedelta(hours=8))


def _log_path() -> Path:
    """Resolve the HindSight recall audit log path."""
    explicit = os.environ.get("CS_OPS_HINDSIGHT_RECALL_LOG", "").strip()
    if explicit:
        return Path(explicit)
    profile_dir = os.environ.get("CS_OPS_PROFILE_DIR", "").strip()
    if profile_dir:
        return Path(profile_dir) / "data" / "hindsight_recall_log.jsonl"
    return Path.home() / ".hermes" / "profiles" / "povison-cs" / "data" / "hindsight_recall_log.jsonl"


def de_escalation_trend(
    *,
    start_bj: str,
    end_bj: str,
) -> dict[str, Any]:
    """Per-day HindSight 减升率 for ``[start_bj, end_bj]`` (Beijing dates, inclusive).

    Returns ``{start_bj, end_bj, available: bool, days: [{date, hs_hit_auto_sessions, hs_escalated_sessions, de_escalation_rate}]}``.
    ``available=False`` when the log is missing/unreadable; caller should hide the metric.
    """
    path = _log_path()
    if not path.exists():
        log.info("metrics_hindsight: recall log not found at %s", path)
        return {"start_bj": start_bj, "end_bj": end_bj, "available": False, "days": []}

    by_day: dict[str, dict[str, set[str]]] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = e.get("timestamp") or ""
                if not ts:
                    continue
                try:
                    d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_BJ_TZ).date().isoformat()
                except ValueError:
                    continue
                if d < start_bj or d > end_bj:
                    continue
                sid = str(e.get("session_id") or "")
                if not sid:
                    continue
                result = str(e.get("result") or "")
                outcome = str(e.get("outcome") or "")
                buckets = by_day.setdefault(d, {"hit_auto": set(), "escalated": set()})
                if result == "hit" and outcome == "auto_handled":
                    buckets["hit_auto"].add(sid)
                if outcome == "escalated":
                    buckets["escalated"].add(sid)
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics_hindsight: failed to read %s: %s", path, exc)
        return {"start_bj": start_bj, "end_bj": end_bj, "available": False, "days": []}

    days = []
    for d in sorted(by_day.keys()):
        hit_auto = by_day[d]["hit_auto"]
        esc = by_day[d]["escalated"]
        days.append({
            "date": d,
            "hs_hit_auto_sessions": len(hit_auto),
            "hs_hit_auto_ids": sorted(hit_auto),
            "hs_tracker_escalated_sessions": len(esc),
            "hs_tracker_escalated_ids": sorted(esc),
        })
    return {"start_bj": start_bj, "end_bj": end_bj, "available": True, "days": days}

