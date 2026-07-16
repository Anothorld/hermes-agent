"""Unassign all QuickCEP sessions for a human operator ("下班" flow).

Uses the operator's own QuickCEP credentials to:
  1. Login → get JWT + userId
  2. List email sessions via POST /im/chatSubSession/list
  3. Filter by operatorIds containing the operator's userId
  4. For each, call quickcep_cli leave-chat (batchLeaveChat) to unassign

The ``batchLeaveChat`` API removes the operator from the chat. If the operator
was the last one in the session, ``chat_end`` fires and the session closes.
If other operators remain, the session stays open — this is the "unassign"
behavior the operator expects.

Token-cache safety: we pass ``--token <jwt>`` to the CLI so the AI account's
cached ``.quickcep_token.json`` is never overwritten.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import Any

from .profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

BASE = "https://app.quickcep.com"
DEFAULT_TIMEOUT = 20
LIST_PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap


def _login(email: str, password: str) -> dict[str, Any]:
    """Login to QuickCEP with the operator's credentials.

    Returns ``{jwt, userId, storeId, staffId}``.
    """
    scripts_dir = quickcep_skill_dir() / "scripts"
    if not (scripts_dir / "quickcep_login.py").is_file():
        raise RuntimeError(f"quickcep_login.py not found at {scripts_dir}")
    import importlib
    sys.path.insert(0, str(scripts_dir))
    try:
        mod = importlib.import_module("quickcep_login")
        auth = mod.quickcep_login(email, password)
    finally:
        sys.path.remove(str(scripts_dir))
    jwt = auth["jwt"]
    claims = _jwt_payload(jwt)
    return {
        "jwt": jwt,
        "userId": str(claims.get("userId") or auth.get("userId") or ""),
        "storeId": int(claims.get("storeId") or auth.get("storeId") or 3371),
        "staffId": str(claims.get("staffId") or ""),
    }


def _jwt_payload(jwt: str) -> dict[str, Any]:
    import base64
    parts = jwt.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.b64decode(payload_b64))


def _list_sessions(jwt: str, *, email_only: bool = True) -> list[dict[str, Any]]:
    """List QuickCEP sessions via the REST API. Returns filtered records."""
    import requests
    headers = {
        "Content-Type": "application/json",
        "quick-token": jwt,
        "Origin": BASE,
        "Referer": f"{BASE}/panel/conversations",
    }
    all_records: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        body: dict[str, Any] = {"pageNumber": page, "pageSize": LIST_PAGE_SIZE}
        if email_only:
            body["viewCondition"] = {
                "conditionRelation": "AND",
                "conditions": [{
                    "conditionFiled": "channels",
                    "conditionOperator": "IN",
                    "conditionValue": [{"channel": "email"}],
                }],
            }
        resp = requests.post(f"{BASE}/im/chatSubSession/list", headers=headers, json=body, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        records = data.get("records") or []
        if email_only:
            records = [r for r in records if (r.get("channel") or "") == "email"]
        all_records.extend(records)
        if not data.get("hasNextPage"):
            break
    return all_records


def _leave_one_session(session_id: str, jwt: str) -> dict[str, Any]:
    """Call quickcep_cli leave-chat with the operator's JWT (no cache overwrite).

    Returns ``{ok, chat_end, error?}``. ``chat_end_not_confirmed`` is treated as
    success for unassign purposes (operator left, session may still be open).
    """
    cli = quickcep_skill_dir() / "scripts" / "quickcep_cli.py"
    if not cli.is_file():
        return {"ok": False, "error": "quickcep_cli_not_found", "chat_end": False}
    try:
        proc = subprocess.run(  # pylint: disable=missing-timeout
            [sys.executable, str(cli), "leave-chat", session_id, "--token", jwt],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "chat_end": False}
    try:
        out = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {}
    # CLI reports ok=False when chat_end doesn't fire, but batchLeaveChat
    # still succeeded (code 200). Treat chat_end_not_confirmed as success.
    if out.get("ok"):
        return {"ok": True, "chat_end": True}
    err = out.get("error") or ""
    if err == "chat_end_not_confirmed":
        return {"ok": True, "chat_end": False, "unassigned": True}
    return {"ok": False, "chat_end": False, "error": err or "leave_failed", "result_code": out.get("result_code")}


def unassign_all_sessions(
    *,
    quickcep_email: str,
    quickcep_password: str,
    env: str = "LIVE",
    operator_id: str = "",
    operator_name: str = "",
) -> dict[str, Any]:
    """Unassign all QuickCEP email sessions for the operator.

    Args:
        quickcep_email: Operator's QuickCEP login email.
        quickcep_password: Operator's QuickCEP login password.
        env: CAL env (for audit logging).
        operator_id: Console operator ID (for audit).
        operator_name: Console operator name (for audit).

    Returns:
        ``{ok, operator_user_id, total_assigned, unassigned, failed, sessions}``
    """
    t0 = time.time()
    # Step 1: Login
    try:
        auth = _login(quickcep_email, quickcep_password)
    except (RuntimeError, ValueError, OSError) as exc:
        log.error("unassign_all: login failed for %s: %s", quickcep_email, exc)
        return {"ok": False, "error": "login_failed", "detail": str(exc)}
    jwt = auth["jwt"]
    op_user_id = auth["userId"]
    if not op_user_id:
        return {"ok": False, "error": "no_user_id_in_jwt"}

    # Step 2: List sessions
    try:
        records = _list_sessions(jwt, email_only=True)
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("unassign_all: list sessions failed: %s", exc)
        return {"ok": False, "error": "list_failed", "detail": str(exc)}

    # Step 3: Filter by operatorIds containing the operator's userId
    assigned = []
    for r in records:
        op_ids = r.get("operatorIds") or []
        if isinstance(op_ids, str):
            op_ids = [op_ids]
        if op_user_id in [str(x) for x in op_ids]:
            assigned.append(r)

    log.info(
        "unassign_all: env=%s operator=%s(%s) qc_user=%s assigned=%d of %d",
        env, operator_name, operator_id, op_user_id, len(assigned), len(records),
    )

    if not assigned:
        return {
            "ok": True,
            "operator_user_id": op_user_id,
            "total_assigned": 0,
            "unassigned": 0,
            "failed": 0,
            "sessions": [],
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    # Step 4: Leave each session
    results = []
    unassigned = 0
    failed = 0
    for r in assigned:
        sid = r.get("id") or r.get("chatSubSessionId") or ""
        if not sid:
            continue
        vi = r.get("visitorInfo") or {}
        label = vi.get("email") or vi.get("firstName") or sid
        res = _leave_one_session(sid, jwt)
        results.append({
            "session_id": sid,
            "email": label,
            "ok": res["ok"],
            "chat_end": res.get("chat_end", False),
            "error": res.get("error"),
        })
        if res["ok"]:
            unassigned += 1
        else:
            failed += 1
        time.sleep(0.3)  # gentle rate limit

    log.info(
        "unassign_all: done env=%s operator=%s(%s) unassigned=%d failed=%d elapsed=%.1fs",
        env, operator_name, operator_id, unassigned, failed, time.time() - t0,
    )

    return {
        "ok": failed == 0,
        "operator_user_id": op_user_id,
        "total_assigned": len(assigned),
        "unassigned": unassigned,
        "failed": failed,
        "sessions": results,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
