"""Tests for inbound poller state helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def state_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugin_root = Path(__file__).resolve().parents[1]
    pkg_name = "kol_ops_bridge_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(plugin_root)]
        sys.modules[pkg_name] = pkg

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.inbound_reply.state",
        plugin_root / "inbound_reply" / "state.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.inbound_reply.state"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_trim_seen_caps_at_2000(state_mod):
    mod = state_mod
    huge = {f"m{i:05d}" for i in range(2500)}
    trimmed = mod.trim_seen(huge)
    assert len(trimmed) == 2000
    assert trimmed[0] == "m00500"
    assert trimmed[-1] == "m02499"


def test_global_message_seen_roundtrip(state_mod, tmp_path: Path, monkeypatch):
    mod = state_mod
    db_path = tmp_path / "console.db"
    conn = __import__("sqlite3").connect(str(db_path))
    conn.execute(
        """CREATE TABLE gmail_poller_global_seen (
            env TEXT NOT NULL,
            message_id TEXT NOT NULL,
            mailbox_user_id INTEGER,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (env, message_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE gmail_poller_watermarks (
            user_id INTEGER PRIMARY KEY,
            last_message_id TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("KOC_DB_PATH", str(db_path))
    mod._CONSOLE_DB_PATH = db_path

    assert mod.global_message_seen(env="TEST", message_id="msg-1") is False
    mod.record_global_message_seen(env="TEST", message_id="msg-1", mailbox_user_id=3)
    assert mod.global_message_seen(env="TEST", message_id="msg-1") is True
    assert mod.global_message_seen(env="LIVE", message_id="msg-1") is False
