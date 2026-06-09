"""Resolve Hermes clarify UI callback without modifying Hermes core.

Hermes injects ``clarify_callback`` on the agent for the built-in ``clarify``
tool only. Plugin tools must discover the platform callback themselves.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


def resolve_clarify_callback(explicit: Optional[Callable] = None) -> Optional[Callable]:
    """Return a callable(question, choices) -> str if the runtime supports clarify."""
    if explicit is not None:
        return explicit

    for resolver in (
        _from_cli_active_agent,
        _from_tui_sessions,
        _from_gateway_running_agent,
        _build_gateway_clarify_callback,
    ):
        try:
            cb = resolver()
        except Exception as exc:
            logger.debug("clarify resolver %s failed: %s", resolver.__name__, exc)
            cb = None
        if cb is not None:
            return cb
    return None


def _from_cli_active_agent() -> Optional[Callable]:
    import cli as cli_mod

    agent = getattr(cli_mod, "_active_agent_ref", None)
    if agent is None:
        return None
    return getattr(agent, "clarify_callback", None)


def _from_tui_sessions() -> Optional[Callable]:
    from gateway.session_context import get_session_env

    import tui_gateway.server as tgw

    bucket = getattr(tgw, "_sessions", None)
    if not isinstance(bucket, dict):
        return None

    for env_key in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY"):
        sid = get_session_env(env_key, "")
        if sid and sid in bucket:
            cb = _agent_clarify_callback(bucket[sid].get("agent"))
            if cb is not None:
                return cb

    # Single active TUI session fallback.
    for sess in bucket.values():
        cb = _agent_clarify_callback(sess.get("agent") if isinstance(sess, dict) else None)
        if cb is not None:
            return cb
    return None


def _agent_clarify_callback(agent) -> Optional[Callable]:
    if agent is None:
        return None
    cb = getattr(agent, "clarify_callback", None)
    return cb if callable(cb) else None


def _gateway_agent_for_session(runner, session_key: str):
    """Return the live gateway agent for *session_key*, if any."""
    if runner is None or not session_key:
        return None

    try:
        from gateway.run import _AGENT_PENDING_SENTINEL
    except ImportError:
        _AGENT_PENDING_SENTINEL = object()

    running = getattr(runner, "_running_agents", None) or {}
    agent = running.get(session_key)
    if agent is not None and agent is not _AGENT_PENDING_SENTINEL:
        return agent

    cache_lock = getattr(runner, "_agent_cache_lock", None)
    cache = getattr(runner, "_agent_cache", None)
    if cache_lock is not None and cache is not None:
        with cache_lock:
            cached = cache.get(session_key)
            if cached:
                return cached[0]
    return None


def _from_gateway_running_agent() -> Optional[Callable]:
    """Gateway path: reuse clarify_callback wired on the running AIAgent.

    Hermes gateway sets ``agent.clarify_callback`` inside ``run_sync`` but does
    not pass it through ``handle_function_call`` kwargs for plugin tools.  Look
    up the in-process ``GatewayRunner`` weakref and the session's running agent
    (same pattern as ``tools/send_message_tool.py``).
    """
    try:
        from tools.approval import get_current_session_key
        from gateway.run import _gateway_runner_ref

        runner = _gateway_runner_ref()
        if runner is None:
            return None

        session_key = get_current_session_key(default="")
        if not session_key:
            from gateway.session_context import get_session_env

            session_key = get_session_env("HERMES_SESSION_KEY", "")

        agent = _gateway_agent_for_session(runner, session_key)
        return _agent_clarify_callback(agent)
    except Exception as exc:
        logger.debug("gateway running-agent clarify lookup failed: %s", exc)
        return None


def _build_gateway_clarify_callback() -> Optional[Callable]:
    from gateway.session_context import get_session_env
    from tools import clarify_gateway as cg

    session_key = get_session_env("HERMES_SESSION_KEY", "")
    if not session_key:
        return None
    if cg.get_notify(session_key) is None:
        return None

    def _callback(question: str, choices: Optional[List[str]]) -> str:
        clarify_id = uuid.uuid4().hex[:10]
        entry = cg.register(
            clarify_id=clarify_id,
            session_key=session_key,
            question=question,
            choices=list(choices) if choices else None,
        )
        notify = cg.get_notify(session_key)
        if notify is None:
            cg.clear_session(session_key)
            return ""
        try:
            notify(entry)
        except Exception as exc:
            logger.warning("gateway clarify notify failed: %s", exc)
            cg.clear_session(session_key)
            return ""

        timeout = float(cg.get_clarify_timeout())
        response = cg.wait_for_response(clarify_id, timeout=timeout)
        if response is None or response == "":
            return f"[user did not respond within {int(timeout / 60)}m]"
        return str(response)

    return _callback
