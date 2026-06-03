"""Tests for kol_bridge_tool --env normalization."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _normalize_env():
    script_dir = Path(__file__).resolve().parents[1] / "scripts"
    path = script_dir / "_cal_client.py"
    spec = importlib.util.spec_from_file_location("_cal_client_env_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.normalize_env


@pytest.fixture
def normalize_env():
    return _normalize_env()


def test_normalize_live_and_test(normalize_env):
    assert normalize_env("LIVE") == "LIVE"
    assert normalize_env("TEST") == "TEST"


def test_normalize_prod_alias(normalize_env):
    assert normalize_env("prod") == "LIVE"
    assert normalize_env("production") == "LIVE"


def test_normalize_dev_alias(normalize_env):
    assert normalize_env("dev") == "TEST"


def test_rejects_unknown(normalize_env):
    with pytest.raises(Exception, match="invalid env"):
        normalize_env("staging")
