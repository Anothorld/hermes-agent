"""Supplement per-campaign budget tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from internal.supplement_ledger import (  # noqa: E402
    SupplementQuotaExceededError,
    assert_supplement_allowed,
    commit_supplement,
)


def test_supplement_cap(nox_home):
    cid = "camp_test_001"
    assert_supplement_allowed(cid, max_calls=2)
    commit_supplement(cid, 1)
    commit_supplement(cid, 1)
    with pytest.raises(SupplementQuotaExceededError):
        assert_supplement_allowed(cid, max_calls=2)
