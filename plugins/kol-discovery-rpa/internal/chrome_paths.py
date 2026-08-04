"""Resolve the shared local-chrome debug profile used for IG cookies.

``start-debug-chrome.sh`` and ``local-chrome-tab-pool`` default to
``$HOME/.hermes/local-chrome-debug-profile`` — a **shared** profile, not one
nested under a Hermes profile's ``HERMES_HOME`` (e.g.
``~/.hermes/profiles/kol-orchestrator/...``).

Using ``$HERMES_HOME/local-chrome-debug-profile`` under kol-orchestrator made
``rpa_download_ig_*`` look for a Cookies DB that does not exist, while CDP
tabs kept using the shared profile's live IG session.
"""

from __future__ import annotations

import os
from pathlib import Path


def default_chrome_profile_dir() -> str:
    """Return the default debug-Chrome user-data dir (env override honored)."""
    override = (os.environ.get("DEBUG_CHROME_PROFILE_DIR") or "").strip()
    if override:
        return override
    return str(Path.home() / ".hermes" / "local-chrome-debug-profile")


def resolve_chrome_cookie_db() -> Path:
    """Return the first existing Chrome Cookies DB among known locations.

    Preference order:
    1. ``$DEBUG_CHROME_PROFILE_DIR/Default/Cookies`` when set
    2. ``$HOME/.hermes/local-chrome-debug-profile/Default/Cookies`` (shared)
    3. ``$HERMES_HOME/local-chrome-debug-profile/Default/Cookies`` (legacy)

    If none exist, returns the preferred path (for error messages).
    """
    candidates: list[Path] = []
    override = (os.environ.get("DEBUG_CHROME_PROFILE_DIR") or "").strip()
    if override:
        candidates.append(Path(override) / "Default" / "Cookies")

    shared = Path.home() / ".hermes" / "local-chrome-debug-profile" / "Default" / "Cookies"
    if shared not in candidates:
        candidates.append(shared)

    hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    nested = hermes_home / "local-chrome-debug-profile" / "Default" / "Cookies"
    if nested not in candidates:
        candidates.append(nested)

    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]
