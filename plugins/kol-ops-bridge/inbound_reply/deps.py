"""Dependency injection ports for inbound reply dispatch."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from .gateway_client import GatewayClient


class BridgeRequestError(RuntimeError):
    """Bridge HTTP or in-process call failed."""


class MatchBridgeError(BridgeRequestError):
    """Bridge unavailable while matching inbound mail to an identity."""


class InboundBridgePort(Protocol):
    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]: ...

    def find_events_for_inbound_match(
        self,
        *,
        env: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        sender_email: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def get_identity(self, identity_id: int) -> dict[str, Any] | None: ...

    def get_facts(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]: ...

    def reply_dispatch_status(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        env: str,
    ) -> dict[str, Any]: ...

    def write_inbound_event(self, body: dict[str, Any]) -> None: ...

    def dispatch_context(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]: ...

    def reply_chase_hint(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        thread_id: str | None,
        env: str,
    ) -> dict[str, Any]: ...


@dataclass
class InboundDeps:
    bridge: InboundBridgePort
    gateway: GatewayClient

    @classmethod
    def in_process_default(cls) -> InboundDeps:
        from ..inbound_reply_ports.in_process import InProcessBridgeAdapter

        return cls(
            bridge=InProcessBridgeAdapter(),
            gateway=GatewayClient.from_env(),
        )

    @classmethod
    def http_default(
        cls,
        *,
        bridge_base: Optional[str] = None,
        bridge_key: Optional[str] = None,
    ) -> InboundDeps:
        from ..inbound_reply_ports.http import HttpBridgeAdapter

        return cls(
            bridge=HttpBridgeAdapter(base=bridge_base, bridge_key=bridge_key),
            gateway=GatewayClient.from_env(),
        )


def legacy_script_enabled() -> bool:
    return os.environ.get("KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT", "0") == "1"


def import_legacy_run_once():
    """Rollback path when ``KOL_OPS_INBOUND_REPLY_LEGACY_SCRIPT=1``."""
    import importlib.util
    from pathlib import Path

    legacy_path = Path(__file__).resolve().parents[1] / "scripts" / "kol_reply_dispatcher_legacy.py"
    spec = importlib.util.spec_from_file_location("kol_reply_dispatcher_legacy", legacy_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_once, mod.GmailUnavailable
