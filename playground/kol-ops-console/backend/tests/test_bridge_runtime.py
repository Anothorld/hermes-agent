from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app import bridge_runtime


def _settings(key: str) -> SimpleNamespace:
    return SimpleNamespace(bridge_key=key)


class BridgeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self._old_env = os.environ.copy()
        self._old_console_env_path = bridge_runtime._console_env_path
        self._old_bridge_secrets_path = bridge_runtime._bridge_secrets_path

        os.environ["HOME"] = str(self.tmp_path)
        os.environ.pop("HERMES_HOME", None)
        for name in bridge_runtime.BRIDGE_KEY_ALIASES:
            os.environ.pop(name, None)
        bridge_runtime._console_env_path = lambda: self.tmp_path / "console.env"
        bridge_runtime._bridge_secrets_path = lambda: self.tmp_path / "secrets.yaml"

    def tearDown(self) -> None:
        bridge_runtime._console_env_path = self._old_console_env_path
        bridge_runtime._bridge_secrets_path = self._old_bridge_secrets_path
        os.environ.clear()
        os.environ.update(self._old_env)
        self._tmp.cleanup()

    def test_resolve_bridge_key_uses_hermes_alias_when_koc_is_placeholder(self) -> None:
        os.environ["HERMES_KOL_OPS_BRIDGE_KEY"] = "real-key"

        self.assertEqual(
            bridge_runtime.resolve_bridge_key(_settings("replace-with-bridge-key")),
            "real-key",
        )

    def test_ensure_gateway_bridge_key_writes_default_and_active_profile(self) -> None:
        default_home = self.tmp_path / ".hermes"
        profile_home = default_home / "profiles" / "kol-orchestrator"
        profile_home.mkdir(parents=True)
        (default_home / "active_profile").write_text(
            "kol-orchestrator",
            encoding="utf-8",
        )

        key = "dev-secret-123"
        self.assertEqual(
            bridge_runtime.ensure_gateway_bridge_key(_settings(key)),
            key,
        )

        self.assertEqual(os.environ["HERMES_KOL_OPS_BRIDGE_KEY"], key)
        self.assertIn(
            f"HERMES_KOL_OPS_BRIDGE_KEY={key}",
            (default_home / ".env").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f"HERMES_KOL_OPS_BRIDGE_KEY={key}",
            (profile_home / ".env").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            oct((default_home / ".env").stat().st_mode & 0o777),
            "0o600",
        )

    def test_ensure_gateway_bridge_key_fails_before_launch_when_missing(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            bridge_runtime.ensure_gateway_bridge_key(_settings("replace-with-bridge-key"))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["error"], "missing_bridge_key")
        self.assertEqual(
            raised.exception.detail["missing"],
            "HERMES_KOL_OPS_BRIDGE_KEY",
        )


if __name__ == "__main__":
    unittest.main()
