"""RpaError.message alias used by download_content_eval error payloads."""

from __future__ import annotations

import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

from errors import DownloadError, RpaError  # noqa: E402


def test_rpa_error_message_alias():
    err = RpaError("dom_changed", "selectors broke")
    assert err.message == "selectors broke"
    assert err.detail == err.message


def test_download_error_message_alias():
    err = DownloadError("cookie_db_not_found", "Chrome cookie DB not found: /x")
    assert err.message == "Chrome cookie DB not found: /x"
    assert "cookie_db_not_found" in str(err)
