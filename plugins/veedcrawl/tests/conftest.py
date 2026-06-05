"""Pytest fixtures for veedcrawl plugin tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _veedcrawl_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEEDCRAWL_API_KEY", "ma_test_key_for_unit_tests")
