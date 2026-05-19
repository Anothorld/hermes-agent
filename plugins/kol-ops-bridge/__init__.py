"""KOL Ops Bridge plugin.

Provides the Conversation Audit Layer (CAL) and the HTTP/SSE bridge that
sits between Hermes skills and the external KOL Ops Console (Web).

See README.md for architecture overview and plan/session notes for the
full design rationale.

Public surface:
- HTTP routes mounted at /api/plugins/kol-ops-bridge/ (see plugin_api.py)
- Python API for skills to call directly (see cal.py)
"""

from __future__ import annotations

__all__ = ["plugin_api", "cal"]
