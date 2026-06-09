"""HTTP bridge adapter — ``CALClient`` for standalone CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from ..inbound_reply.deps import BridgeRequestError

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _cal_client import CALClient, DEFAULT_BASE, KEY_ENV  # noqa: E402


class HttpBridgeAdapter:
    """Bridge port for ``kol_reply_dispatcher`` CLI (separate process)."""

    def __init__(
        self,
        *,
        base: Optional[str] = None,
        bridge_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        import os

        self._client = CALClient(
            base=(base or os.environ.get("HERMES_KOL_OPS_BRIDGE_BASE") or DEFAULT_BASE).rstrip("/"),
            bridge_key=bridge_key or os.environ.get(KEY_ENV),
            timeout=timeout,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._client.request(method, path, **kwargs)
        except SystemExit as exc:
            raise BridgeRequestError(str(exc)) from exc

    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]:
        page = self._request("GET", "/events/recent", params={"env": env, "limit": limit})
        if isinstance(page, dict):
            events = page.get("events")
            return list(events) if isinstance(events, list) else []
        return []

    def get_identity(self, identity_id: int) -> dict[str, Any] | None:
        out = self._request("GET", f"/identities/{identity_id}")
        return out if isinstance(out, dict) else None

    def get_facts(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        raw = self._request(
            "GET",
            f"/facts/{identity_id}",
            params={"campaign_id": campaign_id, "env": env},
        )
        if isinstance(raw, dict):
            inner = raw.get("facts")
            if isinstance(inner, dict):
                return inner
            return raw
        return {}

    def reply_dispatch_status(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        env: str,
    ) -> dict[str, Any]:
        out = self._request(
            "GET",
            f"/identities/{identity_id}/reply-dispatch-status",
            params={
                "campaign_id": campaign_id,
                "message_id": message_id,
                "env": env,
            },
        )
        return out if isinstance(out, dict) else {}

    def write_inbound_event(self, body: dict[str, Any]) -> None:
        self._request("POST", "/events", body=body)

    def dispatch_context(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        out = self._request(
            "GET",
            f"/identities/{identity_id}/dispatch-context",
            params={"campaign_id": campaign_id, "env": env},
        )
        return out if isinstance(out, dict) else {"error": "dispatch_context_unavailable"}

    def reply_chase_hint(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        thread_id: str | None,
        env: str,
    ) -> dict[str, Any]:
        out = self._request(
            "GET",
            f"/identities/{identity_id}/reply-chase-hint",
            params={
                "campaign_id": campaign_id,
                "message_id": message_id,
                "thread_id": thread_id or "",
                "env": env,
            },
        )
        return out if isinstance(out, dict) else {
            "recommended_action": "proceed_normal",
            "prior_pending_draft": False,
        }
