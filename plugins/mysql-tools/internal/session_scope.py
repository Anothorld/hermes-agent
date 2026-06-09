"""Resolve stable session identifiers for mysql-tools approval scope."""

from __future__ import annotations

from typing import Set


def collect_session_keys(hint: str = "") -> Set[str]:
    """Collect every session key that may identify the current Hermes conversation."""
    keys: set[str] = set()
    if hint and str(hint).strip():
        keys.add(str(hint).strip())

    try:
        from tools.approval import get_current_session_key

        approval_key = get_current_session_key(default="")
        if approval_key:
            keys.add(approval_key)
    except Exception:
        pass

    try:
        from gateway.session_context import get_session_env

        for env_name in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY"):
            value = get_session_env(env_name, "")
            if value:
                keys.add(value)
    except Exception:
        pass

    try:
        import cli as cli_mod

        agent = getattr(cli_mod, "_active_agent_ref", None)
        if agent is not None:
            agent_sid = getattr(agent, "session_id", None)
            if agent_sid:
                keys.add(str(agent_sid))
            keys.add(f"agent:{id(agent)}")
    except Exception:
        pass

    try:
        from gateway.session_context import get_session_env

        import tui_gateway.server as tgw

        bucket = getattr(tgw, "_sessions", None)
        if isinstance(bucket, dict):
            for env_name in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY"):
                sid = get_session_env(env_name, "")
                if sid and sid in bucket:
                    agent = bucket[sid].get("agent")
                    agent_sid = getattr(agent, "session_id", None) if agent else None
                    if agent_sid:
                        keys.add(str(agent_sid))
                    if sid:
                        keys.add(sid)
            for sess in bucket.values():
                if not isinstance(sess, dict):
                    continue
                agent = sess.get("agent")
                agent_sid = getattr(agent, "session_id", None) if agent else None
                if agent_sid:
                    keys.add(str(agent_sid))
    except Exception:
        pass

    keys.discard("")
    return keys


def primary_session_key(hint: str = "") -> str:
    """Return one representative session key (for logging/display)."""
    keys = sorted(collect_session_keys(hint))
    return keys[0] if keys else ""
