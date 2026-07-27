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
import importlib
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
        log.info("unassign leave-chat ok session=%s chat_end=True", session_id)
        return {"ok": True, "chat_end": True}
    err = out.get("error") or ""
    if err == "chat_end_not_confirmed":
        log.info("unassign leave-chat ok session=%s chat_end_not_confirmed (unassigned)", session_id)
        return {"ok": True, "chat_end": False, "unassigned": True}
    detail = err or "leave_failed"
    stderr = (proc.stderr or "").strip()
    log.warning(
        "unassign leave-chat failed session=%s error=%s result_code=%s exit=%s stderr=%s",
        session_id, detail, out.get("result_code"), proc.returncode, stderr[:300],
    )
    return {"ok": False, "chat_end": False, "error": detail, "result_code": out.get("result_code"), "stderr": stderr[:500]}


def _import_quickcep_cli():
    """Import the deployed ``quickcep_cli`` module (lives in the skill scripts dir).

    Reuses the same sys.path trick as ``_login`` (which imports ``quickcep_login``).
    Returns the module object so callers can use ``api_request`` /
    ``_connect_operator_socket`` / ``_message_has_chat_end`` directly — this lets
    ``unassign_all`` open ONE Socket.io connection for all sessions instead of
    spawning a subprocess per session.
    """
    scripts_dir = quickcep_skill_dir() / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    return importlib.import_module("quickcep_cli")


def _leave_sessions_via_shared_socket(
    cli_module,
    *,
    jwt: str,
    assigned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Leave every assigned session reusing a single Socket.io connection.

    Opens one SIO polling connection + one ``checkSocket``, then for each session
    runs ``joinChat`` → ``batchLeaveChat`` → poll ``chat_end``. The SIO connection
    is best-effort for ``chat_end`` emission; the REST ``joinChat``/``batchLeaveChat``
    do the actual unassign, so even if the connection goes stale mid-loop the
    operator is still removed from ``operatorIds`` (``chat_end_not_confirmed`` is
    treated as success, matching the legacy subprocess path).

    Raises ``QuickCEPRequestError`` if the initial SIO handshake fails so the
    caller can fall back to the per-session subprocess path.
    """
    api_request = cli_module.api_request
    connect_socket = cli_module._connect_operator_socket
    has_chat_end = cli_module._message_has_chat_end
    QuickCEPRequestError = cli_module.QuickCEPRequestError
    JOIN_TIMEOUT = getattr(cli_module, "JOIN_CHAT_TIMEOUT", 60)
    LEAVE_TIMEOUT = getattr(cli_module, "LEAVE_CHAT_TIMEOUT", 90)

    # One shared SIO connection for the whole batch.
    sock = connect_socket(jwt)
    socket_id = sock["socket_id"]
    store_id = sock["store_id"]
    operator_id = sock["operator_id"]
    staff_id = sock["staff_id"]
    api_request(
        "POST",
        "/im/operator/action/checkSocket",
        jwt,
        {"socketId": socket_id},
        timeout=20,
        api_step="checkSocket",
    )

    results: list[dict[str, Any]] = []
    for r in assigned:
        sid = r.get("id") or r.get("chatSubSessionId") or ""
        if not sid:
            continue
        vi = r.get("visitorInfo") or {}
        label = vi.get("email") or vi.get("firstName") or sid
        res: dict[str, Any] = {
            "session_id": sid,
            "email": label,
            "ok": False,
            "chat_end": False,
        }
        try:
            join = api_request(
                "POST",
                "/im/operator/action/joinChat",
                jwt,
                {"chatSubSessionId": sid},
                timeout=JOIN_TIMEOUT,
                api_step="joinChat",
            )
            if join.get("code") != 200:
                res["error"] = "join_failed"
                res["join_code"] = join.get("code")
                log.warning("unassign shared-socket join_failed session=%s code=%s", sid, join.get("code"))
                results.append(res)
                time.sleep(0.3)
                continue

            leave = api_request(
                "POST",
                "/im/operator/action/batchLeaveChat",
                jwt,
                {
                    "chatSubSessionIds": [sid],
                    "storeId": store_id,
                    "operatorId": operator_id,
                    "staffId": staff_id,
                },
                timeout=LEAVE_TIMEOUT,
                api_step="batchLeaveChat",
            )
            closed = False
            try:
                for _ in range(5):
                    time.sleep(0.35)
                    closed = has_chat_end(jwt, sid)
                    if closed:
                        break
            except Exception as exc:  # noqa: BLE001 — chat_end poll is best-effort
                log.debug("unassign chat_end poll failed session=%s: %s", sid, exc)

            leave_ok = leave.get("code") == 200
            if leave_ok and closed:
                res["ok"] = True
                res["chat_end"] = True
                log.info("unassign shared-socket ok session=%s chat_end=True", sid)
            elif leave_ok and not closed:
                # batchLeaveChat returned 200 but chat_end didn't fire — operator
                # still removed from operatorIds. Treat as unassign success.
                res["ok"] = True
                res["chat_end"] = False
                res["unassigned"] = True
                log.info("unassign shared-socket ok session=%s chat_end_not_confirmed (unassigned)", sid)
            else:
                res["ok"] = False
                res["error"] = "leave_failed"
                res["result_code"] = leave.get("code")
                log.warning(
                    "unassign shared-socket leave_failed session=%s result_code=%s",
                    sid, leave.get("code"),
                )
        except QuickCEPRequestError as exc:
            res["ok"] = False
            res["error"] = str(exc) or "quickcep_request_error"
            log.warning("unassign shared-socket error session=%s: %s", sid, exc)
        except Exception as exc:  # noqa: BLE001 — per-session isolation
            res["ok"] = False
            res["error"] = str(exc) or "unexpected_error"
            log.warning("unassign shared-socket unexpected error session=%s: %s", sid, exc)
        results.append(res)
        time.sleep(0.3)  # gentle rate limit
    return results


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
        return {"ok": False, "error": "no_user_id_in_jwt",
                "detail": "QuickCEP JWT 中缺少 userId，无法识别操作员账号。请检查「设置」里的 QuickCEP 账号是否正确。"}
    # Fix 4: validate staffId early — _connect_operator_socket requires it and
    # would otherwise 100%-fail every leave-chat with an opaque "JWT missing
    # userId/staffId" error. Surface a clear, actionable error instead.
    op_staff_id = auth.get("staffId") or ""
    if not op_staff_id:
        log.error(
            "unassign_all: staffId missing in JWT for %s — cannot open SIO connection",
            quickcep_email,
        )
        return {
            "ok": False,
            "error": "no_staff_id_in_jwt",
            "detail": (
                "QuickCEP 账号缺少 staffId，无法下班退出会话。请改用具备客服坐席权限的 QuickCEP 账号，"
                "或联系管理员为该账号分配坐席后再试。"
            ),
        }

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

    # Step 4: Leave each session. Prefer a single shared Socket.io connection
    # (one handshake + one checkSocket, then joinChat/batchLeaveChat per session)
    # — far faster and more robust than spawning a subprocess per session. Fall
    # back to the per-session subprocess path only if the shared SIO connection
    # cannot be opened (pre-loop). Per-session errors are caught inside the
    # shared path and recorded as individual failures, so a mid-loop raise there
    # would only happen on a code bug — in that case keep whatever partial
    # results we have rather than re-processing already-unassigned sessions.
    results: list[dict[str, Any]] = []
    used_shared_socket = False
    try:
        cli_module = _import_quickcep_cli()
        results = _leave_sessions_via_shared_socket(cli_module, jwt=jwt, assigned=assigned)
        used_shared_socket = True
    except Exception as exc:
        log.warning(
            "unassign_all: shared SIO connection failed (%s); falling back to per-session subprocess",
            exc,
        )

    if not used_shared_socket and not results:
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
            time.sleep(0.3)  # gentle rate limit

    unassigned = sum(1 for r in results if r.get("ok"))
    failed = sum(1 for r in results if not r.get("ok"))

    if used_shared_socket:
        mode = "shared_socket"
    elif results:
        mode = "shared_socket_partial"  # shared path raised mid-loop; kept partial results
    else:
        mode = "subprocess"
    log.info(
        "unassign_all: done env=%s operator=%s(%s) mode=%s unassigned=%d failed=%d elapsed=%.1fs",
        env, operator_name, operator_id,
        mode, unassigned, failed, time.time() - t0,
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
