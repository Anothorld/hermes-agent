"""CLI envelope parsing tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.cli_runner import NoxInsufficientCreditError, _parse_envelope  # noqa: E402


def test_insufficient_credit_raises():
    raw = '{"success": false, "error_code": "INSUFFICIENT_CREDIT"}'
    with pytest.raises(NoxInsufficientCreditError):
        _parse_envelope(raw)


def test_success_envelope():
    raw = '{"success": true, "data": {"ok": 1}}'
    out = _parse_envelope(raw)
    assert out["success"] is True
