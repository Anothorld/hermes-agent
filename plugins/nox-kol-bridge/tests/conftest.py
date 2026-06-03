"""Pytest fixtures for nox-kol-bridge."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def nox_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("NOX_SKIP_CONSOLE_DISPATCH", "1")
    sys.path.insert(0, str(_PLUGIN_ROOT))
    yield home
