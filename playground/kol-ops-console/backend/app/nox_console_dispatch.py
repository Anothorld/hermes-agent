"""Attach signed Nox dispatch claims (shared algorithm with nox-kol-bridge)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[4] / "plugins" / "nox-kol-bridge"


def attach_dispatch_to_config(
    payload: dict[str, Any],
    *,
    campaign_id: str,
    allowed_gates: list[str],
) -> dict[str, Any]:
    """Sign ``nox_console_dispatch`` into the materialized campaign config file."""
    root = _plugin_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from internal.console_dispatch import attach_console_dispatch  # noqa: WPS433

    return attach_console_dispatch(
        payload,
        campaign_id=campaign_id,
        allowed_gates=allowed_gates,
    )


def materialize_with_dispatch(
    campaign_id: str,
    cfg: Mapping[str, Any],
    *,
    allowed_gates: tuple[str, ...],
) -> dict[str, Any]:
    """Build payload dict including campaign_id and console dispatch claim."""
    payload = dict(cfg)
    payload["campaign_id"] = campaign_id
    return attach_dispatch_to_config(
        payload,
        campaign_id=campaign_id,
        allowed_gates=list(allowed_gates),
    )
