"""CAL db_path must follow HERMES_HOME, not a profile-scoped HOME."""

from __future__ import annotations

from pathlib import Path


def test_db_path_prefers_hermes_home_over_profile_home(
    bridge_pkg, monkeypatch, tmp_path: Path,
):
    cal = bridge_pkg.cal
    profile_home = tmp_path / "profiles" / "kol-orchestrator" / "home"
    profile_home.mkdir(parents=True)
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    expected = hermes_home / "kol-ops-bridge" / "cal.db"

    monkeypatch.delenv("HERMES_KOL_OPS_CAL_DB", raising=False)
    monkeypatch.setenv("HOME", str(profile_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    cal.set_db_path(None)

    assert cal.db_path() == expected


def test_db_path_explicit_override_wins(bridge_pkg, monkeypatch, tmp_path: Path):
    cal = bridge_pkg.cal
    override = tmp_path / "custom" / "cal.db"
    monkeypatch.setenv("HERMES_KOL_OPS_CAL_DB", str(override))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    cal.set_db_path(None)

    assert cal.db_path() == override
