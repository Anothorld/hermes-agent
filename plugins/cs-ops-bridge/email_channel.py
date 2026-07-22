"""Email-only channel gate for Povison CS automation."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

EMAIL_CHANNEL = "email"
_DEFAULT_SKILL_DIR = quickcep_skill_dir()


def _quickcep_scripts_dir() -> Path:
    return Path(os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", str(_DEFAULT_SKILL_DIR))) / "scripts"


def normalize_channel(channel: Any) -> str:
    return str(channel or "").strip().lower()


def is_email_channel(channel: Any) -> bool:
    return normalize_channel(channel) == EMAIL_CHANNEL


def inbound_payload_is_email(info: Mapping[str, Any]) -> bool:
    """True when QuickCEP payload is explicitly an email session/message."""
    channel = info.get("channel")
    if channel is not None and str(channel).strip():
        return is_email_channel(channel)
    return False


def cal_session_is_email(sess: Optional[Mapping[str, Any]]) -> bool:
    """True when CAL already recorded this QuickCEP row as an email inbound session."""
    if not sess:
        return False
    if str(sess.get("status") or "").strip().lower() == "skipped":
        return False
    email = str(sess.get("customer_email") or "").strip()
    return bool(email and "@" in email)


def _session_has_email_messages(session_id: str) -> bool:
    """Fallback when list pagination misses aged-out sessions — check message channel."""
    scripts = _quickcep_scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        import quickcep_cli as qc  # type: ignore
    except ImportError as exc:
        log.warning("email channel: quickcep_cli unavailable for messages fallback: %s", exc)
        return False

    try:
        args = argparse.Namespace(token=None, email=None, password=None)
        jwt = qc.get_jwt(args)
    except Exception as exc:
        log.warning("email channel: QuickCEP auth failed for messages fallback %s: %s", session_id, exc)
        return False

    try:
        result = qc.api_request(
            "POST",
            "/im/message/pageOperatorWithNewGroup",
            jwt,
            {"chatSubSessionId": str(session_id), "pageIndex": 0, "pageSize": 20},
            timeout=int(os.environ.get("CS_OPS_EMAIL_MSG_FALLBACK_TIMEOUT", "15")),
            api_step="messages",
        )
    except Exception as exc:
        log.warning("email channel: messages fallback error session %s: %s", session_id, exc)
        return False

    records = (result.get("data") or {}).get("records") or []
    for rec in records:
        if is_email_channel(rec.get("channel")):
            return True
    return False


def fetch_email_session_row(session_id: str) -> Optional[dict[str, Any]]:
    """Load one session from QuickCEP email-channel list; None if not email / not found."""
    scripts = _quickcep_scripts_dir()
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        import quickcep_cli as qc  # type: ignore
    except ImportError as exc:
        log.warning("email channel: quickcep_cli unavailable: %s", exc)
        return None

    try:
        args = argparse.Namespace(token=None, email=None, password=None)
        jwt = qc.get_jwt(args)
    except SystemExit:
        log.warning("email channel: QuickCEP auth failed for session %s", session_id)
        return None
    except Exception as exc:
        log.warning("email channel: QuickCEP auth error session %s: %s", session_id, exc)
        return None

    target = str(session_id)
    max_pages = int(os.environ.get("CS_OPS_INTENT_FETCH_MAX_PAGES", "5"))
    page_size = int(os.environ.get("CS_OPS_INTENT_FETCH_PAGE_SIZE", "100"))

    for page in range(1, max_pages + 1):
        body: dict[str, Any] = {
            "pageNumber": page,
            "pageSize": page_size,
            "viewCondition": {
                "conditionRelation": "AND",
                "conditions": [
                    {
                        "conditionFiled": "channels",
                        "conditionOperator": "IN",
                        "conditionValue": [{"channel": EMAIL_CHANNEL}],
                    }
                ],
            },
            "sort": {"field": "lastMsgTime", "order": "descend"},
        }
        try:
            result = qc.api_request("POST", "/im/chatSubSession/list", jwt, body)
        except Exception as exc:
            log.warning("email channel: list API error session %s page %s: %s", session_id, page, exc)
            return None
        data = result.get("data") or {}
        records = data.get("records") or []
        for rec in records:
            if str(rec.get("id") or "") == target:
                return rec if isinstance(rec, dict) else None
        if not data.get("hasNextPage"):
            break
    return None


def session_is_email(
    quickcep_session_id: str,
    *,
    cal_session: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Verify session belongs to email channel.

    Resolution order:
    1. CAL row with ``customer_email`` (relaunch for aged sessions).
    2. QuickCEP email list pagination (recent inbox rows).
    3. QuickCEP messages API channel probe (list miss on old sessions).
    """
    if cal_session_is_email(cal_session):
        return True
    row = fetch_email_session_row(quickcep_session_id)
    if row:
        return is_email_channel(row.get("channel") or EMAIL_CHANNEL)
    if _session_has_email_messages(quickcep_session_id):
        log.info(
            "email channel: session %s confirmed via messages fallback (list miss)",
            quickcep_session_id,
        )
        return True
    return False
