"""Canonical povison-cs profile paths for cs-ops-bridge."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CS_PROFILE = "povison-cs"


def cs_profile_name() -> str:
    """Hermes profile name used for gateway runs and session_id prefix."""
    name = os.environ.get("CS_OPS_PROFILE", DEFAULT_CS_PROFILE).strip()
    return name or DEFAULT_CS_PROFILE


def _profile_dir_from_hermes_home(hermes_home: Path) -> Path:
    """Map ``HERMES_HOME`` to the CS profile directory.

    Gateway profile runs set ``HERMES_HOME`` to ``<root>/profiles/<name>``.
    Root/default runs use ``<root>`` and keep profiles under ``profiles/<name>``.
    """
    name = cs_profile_name()
    if hermes_home.name == name:
        return hermes_home
    return hermes_home / "profiles" / name


def cs_profile_dir() -> Path:
    """Resolved HERMES_HOME directory for the CS agent profile."""
    explicit = os.environ.get("CS_OPS_PROFILE_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return _profile_dir_from_hermes_home(Path(hermes_home).expanduser())

    return Path.home() / ".hermes" / "profiles" / cs_profile_name()


def quickcep_skill_dir() -> Path:
    """QuickCEP skill root (CLI + SIO monitor scripts)."""
    override = os.environ.get("CS_OPS_QUICKCEP_SKILL_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return cs_profile_dir() / "skills" / "social-media" / "quickcep"


def gateway_session_id(*, env: str, quickcep_session_id: str) -> str:
    """Gateway run session_id — must match the profile running the agent."""
    return f"{cs_profile_name()}:{env}:{quickcep_session_id}"


def hindsight_bridge_script() -> Path:
    """Bundled hindsight_bridge.py (shared under HERMES home, not profile-local)."""
    override = os.environ.get("CS_OPS_HINDSIGHT_BRIDGE_SCRIPT", "").strip()
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        candidate = Path(hermes_home).expanduser() / "skills" / "hindsight-memory" / "scripts" / "hindsight_bridge.py"
        if candidate.is_file():
            return candidate
    return Path.home() / ".hermes" / "skills" / "hindsight-memory" / "scripts" / "hindsight_bridge.py"


def hindsight_bank_id() -> str:
    """Default Hindsight bank for CS escalation Q&A retain."""
    return (os.environ.get("CS_OPS_HINDSIGHT_BANK") or "povison-cs-hermes-user").strip() or "povison-cs-hermes-user"


def hindsight_recall_tracker_script() -> Path:
    """Hindsight recall success tracker script (profile-local)."""
    override = os.environ.get("CS_OPS_HINDSIGHT_RECALL_TRACKER", "").strip()
    if override:
        return Path(override).expanduser()
    return cs_profile_dir() / "skills" / "customer-service" / "povison-cs-orchestrator-flow" / "scripts" / "hindsight_recall_tracker.py"


def assert_expected_profile(*, context: str = "cs-ops-bridge") -> None:
    """Log a warning when CS_OPS_PROFILE is not the default povison-cs profile."""
    name = cs_profile_name()
    if name != DEFAULT_CS_PROFILE:
        log.warning(
            "%s: CS_OPS_PROFILE=%r (default is %r); verify gateway uses the same profile",
            context,
            name,
            DEFAULT_CS_PROFILE,
        )
