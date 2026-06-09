"""Human approval gate for SQL execution via Hermes clarify."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from tools.clarify_tool import clarify_tool

_lock = threading.Lock()
_session_skip: set[str] = set()
_resolve_clarify_callback_fn: Optional[Callable[..., Optional[Callable]]] = None
_collect_session_keys_fn: Optional[Callable[[str], set[str]]] = None
_primary_session_key_fn: Optional[Callable[[str], str]] = None


def _get_session_scope():
    """Load sibling session_scope (supports direct module load in tests)."""
    global _collect_session_keys_fn, _primary_session_key_fn
    if _collect_session_keys_fn is not None and _primary_session_key_fn is not None:
        return _collect_session_keys_fn, _primary_session_key_fn
    try:
        from .session_scope import collect_session_keys, primary_session_key

        _collect_session_keys_fn = collect_session_keys
        _primary_session_key_fn = primary_session_key
        return _collect_session_keys_fn, _primary_session_key_fn
    except ImportError:
        path = Path(__file__).resolve().with_name("session_scope.py")
        name = "hermes_plugins.mysql_tools.internal.session_scope"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load session_scope from {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _collect_session_keys_fn = mod.collect_session_keys
        _primary_session_key_fn = mod.primary_session_key
        return _collect_session_keys_fn, _primary_session_key_fn

CHOICE_APPROVE_ONCE = "批准本次执行"
CHOICE_DENY = "拒绝"
CHOICE_SESSION_SKIP = "批准本会话内免审"


def _get_resolve_clarify_callback():
    """Load sibling clarify_resolver without requiring Hermes core changes."""
    global _resolve_clarify_callback_fn
    if _resolve_clarify_callback_fn is not None:
        return _resolve_clarify_callback_fn
    try:
        from .clarify_resolver import resolve_clarify_callback

        _resolve_clarify_callback_fn = resolve_clarify_callback
        return _resolve_clarify_callback_fn
    except ImportError:
        path = Path(__file__).resolve().with_name("clarify_resolver.py")
        name = "hermes_plugins.mysql_tools.internal.clarify_resolver"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load clarify_resolver from {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _resolve_clarify_callback_fn = mod.resolve_clarify_callback
        return _resolve_clarify_callback_fn


def clear_session(session_id: str = "") -> None:
    """Drop session-scoped skip approval for a session boundary."""
    collect_session_keys, _ = _get_session_scope()
    keys = collect_session_keys(session_id)
    if not keys:
        return
    with _lock:
        _session_skip.difference_update(keys)


def is_session_skipped(session_id: str = "") -> bool:
    """Return True when the user chose session-wide skip for this session."""
    collect_session_keys, _ = _get_session_scope()
    keys = collect_session_keys(session_id)
    if not keys:
        return False
    with _lock:
        return bool(_session_skip.intersection(keys))


def mark_session_skipped(session_id: str = "") -> None:
    """Remember session-wide skip for all keys identifying this conversation."""
    collect_session_keys, _ = _get_session_scope()
    keys = collect_session_keys(session_id)
    if not keys:
        return
    with _lock:
        _session_skip.update(keys)


def _normalize_response(raw: str) -> str:
    return (raw or "").strip().lower()


def _is_clarify_timeout(user_response: str) -> bool:
    """True when clarify timed out or the operator never responded."""
    stripped = (user_response or "").strip()
    if not stripped:
        return True
    normalized = stripped.lower()
    timeout_markers = (
        "[user did not respond",
        "did not provide a response within the time limit",
        "did not respond within",
        "clarify timed out",
    )
    return any(marker in normalized for marker in timeout_markers)


def _parse_clarify_response(result_json: str) -> str:
    try:
        payload = json.loads(result_json)
    except json.JSONDecodeError:
        return ""
    if payload.get("error"):
        return ""
    return str(payload.get("user_response", "")).strip()


def _map_choice_response(user_response: str, choices: list[str]) -> str:
    """Map numeric clarify answers (1/2/3) back to choice labels."""
    stripped = (user_response or "").strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    return stripped


def request_sql_execution_approval(
    *,
    sql: str,
    database: Optional[str],
    session_id: str = "",
    clarify_callback=None,
) -> Tuple[bool, str]:
    """Ask the operator to approve SQL execution via clarify.

    Returns:
        (approved, message) — message is empty when approved.
    """
    if not session_id:
        _, primary_session_key = _get_session_scope()
        session_id = primary_session_key()

    if is_session_skipped(session_id):
        return True, ""

    callback = _get_resolve_clarify_callback()(clarify_callback)
    if callback is None:
        return False, (
            "SQL 执行需要人工审核，但当前环境未提供 clarify 交互能力。"
            "请在启用 clarify 工具集的交互式 Hermes 会话中使用 mysql_execute_sql。"
        )

    db_label = database or "(默认库)"
    preview_sql = sql.strip()
    if len(preview_sql) > 1200:
        preview_sql = preview_sql[:1200] + "\n…(已截断)"

    question = (
        "即将执行 MySQL 查询，请审核 SQL 后再继续。\n\n"
        f"目标库: {db_label}\n\n"
        f"SQL:\n{preview_sql}"
    )
    choices = [CHOICE_APPROVE_ONCE, CHOICE_DENY, CHOICE_SESSION_SKIP]

    result_json = clarify_tool(question=question, choices=choices, callback=callback)
    user_response = _parse_clarify_response(result_json)
    if _is_clarify_timeout(user_response):
        return False, "SQL 执行已取消：审核超时，已自动拒绝。"

    if not user_response:
        return False, "SQL 执行已取消：未收到有效的人工审核回复。"

    user_response = _map_choice_response(user_response, choices)
    normalized = _normalize_response(user_response)

    if CHOICE_DENY.lower() in normalized or normalized in {"拒绝", "deny", "no", "n", "2"}:
        return False, "SQL 执行已取消：操作员拒绝本次查询。"

    if (
        CHOICE_SESSION_SKIP.lower() in normalized
        or "免审" in user_response
        or "session" in normalized
        or normalized in {"3"}
    ):
        mark_session_skipped(session_id)
        return True, ""

    if (
        CHOICE_APPROVE_ONCE.lower() in normalized
        or (normalized.startswith("批准") and "免审" not in user_response)
        or normalized in {"approve", "yes", "y", "ok", "1"}
    ):
        return True, ""

    return False, f"SQL 执行已取消：无法识别的审核回复 “{user_response}”。"
