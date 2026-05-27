"""App configuration via pydantic-settings.

All knobs are env-driven with the ``KOC_`` prefix.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_bridge_key() -> str:
    return os.environ.get("HERMES_KOL_OPS_BRIDGE_KEY", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KOC_", extra="ignore")

    # --- Local state ---
    db_path: Path = Field(
        default=Path("~/.hermes/kol-ops-console/app.db").expanduser(),
        description="SQLite file for console-local state.",
    )

    # --- JWT ---
    jwt_secret: str = Field(default="dev-only-change-me", min_length=16)
    jwt_alg: str = "HS256"
    jwt_ttl_sec: int = 60 * 60 * 8  # 8 hours

    # --- Hermes bridge plugin ---
    bridge_base: str = "http://127.0.0.1:8080/api/plugins/kol-ops-bridge"
    bridge_key: str = Field(default_factory=_default_bridge_key)
    # Must clear the bridge's GmailClient subprocess timeout (30s in
    # gmail_client.py) plus the surrounding DB writes / event logging in
    # _approve_or_reject — a 5s margin was too tight and caused console
    # 502s while Gmail draft creation was still in flight. 60s leaves a
    # comfortable 30s for the rest of the handler. Plain reads stay
    # sub-second; this only matters for the Gmail-touching writes.
    bridge_timeout_sec: float = 60.0

    # --- Hermes gateway ---
    gateway_base: str = "http://127.0.0.1:8642"
    gateway_key: str = ""

    # --- App ---
    env: str = "LIVE"  # default env to query when client omits it; LIVE|TEST
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
