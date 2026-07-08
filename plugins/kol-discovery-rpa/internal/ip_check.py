"""IP preflight check — verify US exit IP before any IG navigation.

Replaces the skill's manual ``browser_navigate(ipinfo.io)`` first step.
Uses ``cdp_page`` to navigate to ipinfo.io/json and parse the response.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Hyphenated directory can't use package imports
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import RpaError  # noqa: E402


def check_ip(runner, expected_country: str = "US") -> dict:
    """Navigate to ipinfo.io/json and verify the exit IP country.

    Args:
        runner: A ``CdpRunner`` instance.
        expected_country: ISO country code to check (default "US").

    Returns:
        Dict with ``ip``, ``country``, ``org``, ``ok`` (True if matches).

    Raises:
        RpaError: If ipinfo.io is unreachable or response is unparseable.
    """
    resp = runner.navigate("https://ipinfo.io/json")
    if not resp.get("success"):
        raise RpaError("ip_check_failed", f"navigate failed: {resp.get('error')}")

    # ipinfo.io/json returns JSON in the body text
    import json
    data = resp.get("data", {})
    text = data.get("snapshot", "") or data.get("text", "")

    try:
        # The snapshot may have extra text; extract JSON object
        text_stripped = text.strip()
        # Find the first { and last }
        start = text_stripped.find("{")
        end = text_stripped.rfind("}")
        if start == -1 or end == -1:
            raise RpaError("ip_check_failed", "no JSON in ipinfo response")
        info = json.loads(text_stripped[start : end + 1])
    except json.JSONDecodeError as e:
        raise RpaError("ip_check_failed", f"JSON parse error: {e}")

    country = info.get("country", "")
    ip = info.get("ip", "")
    org = info.get("org", "")

    return {
        "ip": ip,
        "country": country,
        "org": org,
        "ok": country.upper() == expected_country.upper(),
        "expected": expected_country.upper(),
    }
