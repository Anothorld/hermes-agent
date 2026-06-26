"""Gate inbound processing on QuickCEP AI intention tags (intentionTags)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DEBUG_LOG_PATH = Path("/Users/arnold/agent_prj/.cursor/debug-400546.log")
_DEBUG_SESSION_ID = "400546"


def _agent_debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": _DEBUG_SESSION_ID,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "intent_filter.yaml"


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {"enabled": True, "allowed_intention_tags": ["产品咨询", "物流咨询"]}
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def intent_filter_enabled() -> bool:
    env = os.environ.get("CS_OPS_INTENT_FILTER")
    if env is not None:
        return _truthy(env, default=True)
    return bool(_load_config().get("enabled", True))


def allowed_intention_tags() -> frozenset[str]:
    env = os.environ.get("CS_OPS_ALLOWED_INTENTION_TAGS")
    if env is not None:
        tags = [t.strip() for t in env.split(",") if t.strip()]
        return frozenset(tags)
    raw = _load_config().get("allowed_intention_tags") or ["产品咨询", "物流咨询"]
    return frozenset(str(t).strip() for t in raw if str(t).strip())


@dataclass(frozen=True)
class IntentGateResult:
    allowed: bool
    reason: str
    tags: tuple[str, ...]


def normalize_intention_tags(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def matches_allowed_intention(tags: tuple[str, ...]) -> bool:
    if not tags:
        return False
    allowed = allowed_intention_tags()
    return any(tag in allowed for tag in tags)


def fetch_session_intention_tags(session_id: str) -> tuple[str, ...]:
    """Load intentionTags for one email session via QuickCEP session list API."""
    from .email_channel import fetch_email_session_row

    row = fetch_email_session_row(session_id)
    if not row:
        return ()
    return normalize_intention_tags(row.get("intentionTags"))


def check_intent_gate(
    session_id: str,
    intention_tags: Any = None,
    *,
    fetch_if_missing: bool = True,
    customer_email: str | None = None,
    env: str = "LIVE",
) -> IntentGateResult:
    """Return whether watcher should launch automation for this session."""
    if not intent_filter_enabled():
        return IntentGateResult(True, "filter_disabled", ())

    tags = normalize_intention_tags(intention_tags)
    if not tags and fetch_if_missing and session_id:
        tags = fetch_session_intention_tags(session_id)

    if not tags:
        from . import cal

        if customer_email and cal.has_prior_session_for_email(
            customer_email=customer_email,
            env=env,
            exclude_quickcep_session_id=session_id or None,
        ):
            _agent_debug_log(
                hypothesis_id="F",
                location="intent_gate.py:check_intent_gate",
                message="prior customer bypass without intention tags",
                data={
                    "quickcep_session_id": session_id,
                    "customer_email_domain": customer_email.split("@")[-1] if "@" in customer_email else "",
                },
            )
            return IntentGateResult(True, "prior_customer_no_intent_tags", ())
        _agent_debug_log(
            hypothesis_id="F",
            location="intent_gate.py:check_intent_gate",
            message="blocked no intention tags",
            data={"quickcep_session_id": session_id, "has_customer_email": bool(customer_email)},
        )
        return IntentGateResult(False, "no_intention_tags", ())

    if matches_allowed_intention(tags):
        return IntentGateResult(True, "allowed", tags)

    allowed = ", ".join(sorted(allowed_intention_tags()))
    return IntentGateResult(False, f"intention_not_allowed (allowed: {allowed})", tags)
