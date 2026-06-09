"""Gmail inbound reply polling — identity match, event write, gateway dispatch."""

from __future__ import annotations

from .gating import resolve_autoflow_controls
from .schemas import IdentityMatch, InboundTickStats, ProcessStatus

__all__ = [
    "IdentityMatch",
    "InboundTickStats",
    "ProcessStatus",
    "resolve_autoflow_controls",
    "run_once",
]

INBOUND_MODULE_VERSION = "1.0.0"


def run_once(*args, **kwargs):
    """Lazy import avoids circular load with processor/orchestrator."""
    from .orchestrator import run_once as _run_once

    return _run_once(*args, **kwargs)
