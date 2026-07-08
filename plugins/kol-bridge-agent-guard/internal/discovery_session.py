"""Per-run discovery bootstrap + campaign binding helpers for agent guard."""

from __future__ import annotations

import re
import threading
from typing import Final, Optional
from urllib.parse import urlparse

_BOOTSTRAP_STEPS: Final[frozenset[str]] = frozenset(
    {"list_candidates", "skip_handles", "cooldown_handles"}
)

# task_id / session_id → completed bootstrap steps (in-process; one gateway worker).
_bootstrap_done: dict[str, set[str]] = {}

# RPA fallback tokens: (task_id, normalized_url) → True (one-shot, consumed on use).
# When an RPA tool returns ok=false with a fallback hint, it grants a token
# so the guard allows ONE browser_navigate to the same URL. Agent cannot self-grant.
_rpa_fallback_tokens: dict[tuple[str, str], bool] = {}
_fallback_lock = threading.Lock()

_SESSION_RE = re.compile(r"^kol-campaign:(TEST|LIVE):(.+)$")
_CAMPAIGN_ID_RE = re.compile(r"--campaign-id\s+(\S+)")
_ENV_RE = re.compile(r"--env\s+(TEST|LIVE)\b")
_BRIDGE_CLI_MARKERS = ("kol_bridge_tool.py", "kol-bridge-cli")


def parse_campaign_session(session_key: str) -> Optional[tuple[str, str]]:
    """Return ``(env, campaign_id)`` for ``kol-campaign:{env}:{campaign_id}``."""
    key = (session_key or "").strip()
    match = _SESSION_RE.match(key)
    if not match:
        return None
    return match.group(1), match.group(2)


def is_bridge_cli_command(command: str) -> bool:
    text = command or ""
    return any(marker in text for marker in _BRIDGE_CLI_MARKERS)


def extract_campaign_id_from_command(command: str) -> Optional[str]:
    match = _CAMPAIGN_ID_RE.search(command or "")
    return match.group(1).strip() if match else None


def extract_env_from_command(command: str) -> Optional[str]:
    match = _ENV_RE.search(command or "")
    return match.group(1) if match else None


def classify_bootstrap_step(command: str) -> Optional[str]:
    """Map a bridge CLI command to a bootstrap step name, if applicable."""
    if not is_bridge_cli_command(command):
        return None
    text = command or ""
    if "list-candidates" in text:
        return "list_candidates"
    if "list-discovery-skip-handles" in text:
        return "skip_handles"
    if "list-outreach-cooldown-handles" in text:
        return "cooldown_handles"
    return None


def mark_bootstrap_step(session_key: str, step: str) -> None:
    if step not in _BOOTSTRAP_STEPS:
        return
    _bootstrap_done.setdefault(session_key, set()).add(step)


def bootstrap_complete(session_key: str) -> bool:
    return _BOOTSTRAP_STEPS <= _bootstrap_done.get(session_key, set())


def missing_bootstrap_steps(session_key: str) -> list[str]:
    done = _bootstrap_done.get(session_key, set())
    return sorted(_BOOTSTRAP_STEPS - done)


def reset_bootstrap(session_key: str) -> None:
    _bootstrap_done.pop(session_key, None)


def validate_campaign_binding(session_key: str, command: str) -> Optional[str]:
    """Return a block message when ``--campaign-id`` / ``--env`` disagree with session."""
    parsed = parse_campaign_session(session_key)
    if parsed is None:
        return None
    expected_env, expected_campaign_id = parsed
    if not is_bridge_cli_command(command):
        return None

    campaign_id = extract_campaign_id_from_command(command)
    if campaign_id and campaign_id != expected_campaign_id:
        return (
            f"Bridge CLI --campaign-id {campaign_id!r} does not match this run's session "
            f"campaign {expected_campaign_id!r} (session {session_key}). "
            f"Use --campaign-id {expected_campaign_id} --env {expected_env} only."
        )

    env = extract_env_from_command(command)
    if env and env != expected_env:
        return (
            f"Bridge CLI --env {env!r} does not match session env {expected_env!r}. "
            f"Use --env {expected_env} --campaign-id {expected_campaign_id}."
        )
    return None


def bootstrap_block_message(session_key: str) -> str:
    parsed = parse_campaign_session(session_key)
    env, campaign_id = parsed if parsed else ("LIVE", "<campaign_id>")
    missing = missing_bootstrap_steps(session_key)
    steps = "\n".join(
        f"  {idx}. python3 -u plugins/kol-ops-bridge/scripts/kol_bridge_tool.py {cmd}"
        for idx, cmd in enumerate(
            (
                f"list-candidates --env {env} --campaign-id {campaign_id}",
                f"list-discovery-skip-handles --env {env}",
                f"list-outreach-cooldown-handles --env {env} --plain",
            ),
            start=1,
        )
    )
    return (
        "Discovery bootstrap incomplete — browser tools are blocked until you run ALL "
        "three bridge preflight calls for THIS campaign and print exclusion stats "
        "(CAL candidate count, skip-handle count, cooldown count) in your next message.\n"
        f"Session: {session_key}\n"
        f"Required (missing: {', '.join(missing) or 'none'}):\n{steps}\n"
        "Do NOT use browser_navigate or veedcrawl_* until bootstrap is done."
    )


# ----------------------------------------------------------------- RPA fallback tokens

def _normalize_url(url: str) -> str:
    """Normalize a URL for token keying (strip trailing slash, lowercase host)."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url.strip().lower().rstrip("/")


def grant_rpa_fallback(session_key: str, url: str) -> None:
    """Grant a one-shot fallback token allowing ONE browser_navigate to this URL.

    Called by RPA tools when they return ok=false with a fallback hint.
    The Agent cannot self-grant — only RPA tool handlers call this.
    """
    if not url:
        return
    norm = _normalize_url(url)
    with _fallback_lock:
        _rpa_fallback_tokens[(session_key, norm)] = True


def consume_rpa_fallback(session_key: str, url: str) -> bool:
    """Check and consume a fallback token. Returns True if a token was found.

    Called by the guard hook before blocking a browser_navigate.
    If a token exists for (session, url), it is consumed (deleted) and
    the browser call is allowed. Subsequent calls to the same URL will block.
    """
    if not url:
        return False
    norm = _normalize_url(url)
    with _fallback_lock:
        return _rpa_fallback_tokens.pop((session_key, norm), False)


def reset_fallback_tokens(session_key: str) -> None:
    """Clear all fallback tokens for a session (called on run end)."""
    with _fallback_lock:
        keys_to_remove = [k for k in _rpa_fallback_tokens if k[0] == session_key]
        for k in keys_to_remove:
            del _rpa_fallback_tokens[k]
