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
    gmail_token_secret: str = Field(
        default="",
        description=(
            "Dedicated secret for encrypting Gmail OAuth tokens at rest. "
            "Falls back to jwt_secret when unset. Prefer rotating this "
            "independently of JWT signing keys."
        ),
    )
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
    # Approve-shortlist route-discovery runs select + recompute_goals per identity;
    # 15+ KOL batches can exceed the default 60s bridge timeout.
    bridge_approve_timeout_sec: float = 180.0
    # LLM style distill can take 2+ minutes (10 samples + Hermes call_llm).
    bridge_learning_timeout_sec: float = 300.0

    # --- Hermes gateway ---
    gateway_base: str = "http://127.0.0.1:8642"
    gateway_key: str = ""
    # Optional session-scoped YOLO for unattended Gateway runs. Keep disabled
    # by default; set KOC_GATEWAY_YOLO=true only for trusted automation.
    gateway_yolo: bool = False

    # --- App ---
    env: str = "LIVE"  # default env to query when client omits it; LIVE|TEST
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Google OAuth for per-operator Gmail (Desktop app or Web client JSON).
    google_client_secret_path: Path | None = Field(
        default=None,
        description="Path to Google OAuth client_secret JSON; falls back to HERMES_HOME or cert/",
    )
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8765/auth/google/callback",
        description="Must match a redirect URI registered in Google Cloud Console.",
    )
    gmail_tokens_dir: Path = Field(
        default=Path("~/.hermes/kol-ops/gmail_tokens").expanduser(),
        description="Per-user token files consumed by kol-ops-bridge GmailClient.",
    )
    internal_api_key: str = Field(
        default="",
        description="Shared secret for /internal/* (poller, bridge). Falls back to bridge_key.",
    )
    # Slow API diagnostics (dev aid). Log only when a request takes longer
    # than this threshold; disabled by default to keep prod logs quiet.
    slow_api_log_enabled: bool = False
    slow_api_log_threshold_sec: float = 0.3
    slow_api_log_paths: tuple[str, ...] = (
        "/campaigns",
        "/approvals",
        "/kols",
        "/products",
        "/gateway-approvals",
        "/learning",
    )

    # --- Performance / concurrency ---
    gateway_launch_queue_enabled: bool = True
    gateway_launch_max_inflight: int = 8
    recovery_launch_serial: bool = True
    run_reconciler_enabled: bool = True
    run_reconciler_interval_sec: float = 20.0
    sync_run_states_on_get: bool = False
    run_status_cache_ttl_sec: float = 10.0
    run_status_cache_active_ttl_sec: float = 3.0
    approval_watch_mode: str = "auto"  # auto | sse_per_run | poll_aggregate
    approval_watch_poll_threshold: int = 5
    agent_stream_max_runs: int = 10
    nox_max_concurrent: int = 2
    learning_async_jobs: bool = True
    bridge_gmail_poller_in_executor: bool = True
    brief_compact_contract: bool = True
    nox_batch_async: bool = True
    nox_batch_async_min_ids: int = 5
    launch_http_202: bool = True
    launch_bridge_health_check: bool = True

    # --- Discovery decision learning ---
    # Require reason tags (+ early-phase comment) on shortlist approve /
    # remove / transfer. Disable (KOC_DISCOVERY_FEEDBACK_REQUIRED=false) only
    # as an emergency rollback — learning samples stop accumulating.
    discovery_feedback_required: bool = True
    # Inject learned discovery criteria (SPU + category policies) into the
    # discovery launch / rediscover brief.
    discovery_learned_criteria: bool = True
    discovery_learned_criteria_max_chars: int = 4000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
