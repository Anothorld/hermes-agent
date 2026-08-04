"""Session health check — verify IG login session before batch profile visits.

Checks the local-chrome-debug-profile for a valid ``sessionid`` cookie
and optionally navigates to ``instagram.com`` to confirm the page loads
as a logged-in feed (not a login wall). This prevents burning profile-visit
quota with an expired session, which would trigger IG risk systems.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import SessionExpiredError  # noqa: E402

try:
    from chrome_paths import default_chrome_profile_dir, resolve_chrome_cookie_db
except ImportError:  # pragma: no cover
    from .chrome_paths import default_chrome_profile_dir, resolve_chrome_cookie_db  # type: ignore

_CHROME_PROFILE = default_chrome_profile_dir()
# Shared debug-Chrome Cookies DB (same profile as tab-pool / start-debug-chrome.sh)
_COOKIE_DB = str(resolve_chrome_cookie_db())

# sessionid is IG's primary auth cookie; without it, the session is dead
_IG_COOKIE_DOMAIN = ".instagram.com"


def check_cookie_alive(max_age_hours: float = 720.0) -> dict:
    """Check if the IG sessionid cookie exists and is not expired.

    Args:
        max_age_hours: Max acceptable age in hours (default 30 days).

    Returns:
        Dict with ``ok``, ``has_sessionid``, ``cookie_age_hours`` (or None).
    """
    db_path = Path(_COOKIE_DB)
    if not db_path.exists():
        return {"ok": False, "has_sessionid": False, "reason": "cookie_db_not_found",
                "path": str(db_path)}

    # Chrome locks the cookie DB; copy to temp for read-only access
    import tempfile, shutil
    tmp = Path(tempfile.gettempdir()) / f"chrome_cookies_{os.getpid()}.db"
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(str(tmp))
        cursor = conn.execute(
            "SELECT name, expires_utc FROM cookies "
            "WHERE host_key = ? AND name = 'sessionid' LIMIT 1",
            (_IG_COOKIE_DOMAIN,),
        )
        row = cursor.fetchone()
        conn.close()
    except Exception as e:
        return {"ok": False, "has_sessionid": False, "reason": f"cookie_read_error: {e}"}
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    if row is None:
        return {"ok": False, "has_sessionid": False, "reason": "sessionid_not_found"}

    name, expires_utc = row
    # Chrome stores expires_utc as microseconds since 1601-01-01
    # Convert to Unix epoch seconds
    if expires_utc > 0:
        unix_expires = expires_utc / 1_000_000 - 11644473600
        if unix_expires < time.time():
            return {"ok": False, "has_sessionid": False, "reason": "sessionid_expired"}

    return {"ok": True, "has_sessionid": True}


def check_session(runner) -> dict:
    """Navigate to instagram.com and check if we land on feed (logged in).

    Args:
        runner: A ``CdpRunner`` instance.

    Returns:
        Dict with ``ok``, ``logged_in``, and ``risk`` (if detected).

    Raises:
        SessionExpiredError: If login wall detected.
    """
    from risk_detector import detect_risk

    resp = runner.navigate("https://www.instagram.com/")
    if not resp.get("success"):
        return {"ok": False, "logged_in": False, "reason": f"navigate failed: {resp.get('error')}"}

    data = resp.get("data", {})
    text = data.get("snapshot", "")
    risk = detect_risk(text)

    if risk == "session_expired":
        raise SessionExpiredError("IG login wall detected — re-login debug Chrome")
    if risk == "checkpoint":
        from errors import CheckpointError
        raise CheckpointError("checkpoint on instagram.com landing")

    # If we see feed markers, we're logged in
    text_lower = text.lower()
    logged_in = any(
        marker in text_lower for marker in ("home", "reels", "explore", "direct")
    ) and "log in" not in text_lower[:500]

    return {"ok": True, "logged_in": logged_in, "risk": risk}
