"""In-process bridge adapter — direct ``cal.*`` calls (no loopback HTTP)."""

from __future__ import annotations

from typing import Any

from .. import cal
from ..internal.dispatch_context_bundle import build_dispatch_context_bundle
from ..inbound_reply.deps import BridgeRequestError


class InProcessBridgeAdapter:
    """Bridge port for ``gmail_worker`` running inside ``serve.py``."""

    def list_recent_events(self, *, env: str, limit: int) -> list[dict[str, Any]]:
        return cal.list_events(env=env, limit=limit)

    def find_events_for_inbound_match(
        self,
        *,
        env: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        sender_email: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return cal.find_events_for_inbound_match(
            env=env,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            sender_email=sender_email,
            limit=limit,
        )

    def get_identity(self, identity_id: int) -> dict[str, Any] | None:
        return cal.get_identity(identity_id)

    def get_facts(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        if not cal.get_identity(identity_id):
            return {}
        facts = cal.latest_facts_for(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
        )
        return facts if isinstance(facts, dict) else {}

    def reply_dispatch_status(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        env: str,
    ) -> dict[str, Any]:
        if not cal.get_identity(identity_id):
            return {}
        return cal.reply_dispatch_status(
            identity_id=identity_id,
            campaign_id=campaign_id,
            message_id=message_id,
            env=env,
        )

    def write_inbound_event(self, body: dict[str, Any]) -> None:
        identity_id = int(body["identity_id"])
        if not cal.get_identity(identity_id):
            raise BridgeRequestError(f"identity not found: {identity_id}")
        event_type = str(body["event_type"])
        campaign_id = body.get("campaign_id")
        payload = body.get("payload")
        env = str(body.get("env") or "LIVE")
        event_id = cal.write_event(
            identity_id=identity_id,
            event_type=event_type,
            actor=str(body.get("actor") or "cron"),
            campaign_id=campaign_id,
            goal=body.get("goal"),
            lane=body.get("lane"),
            payload=payload if isinstance(payload, dict) else {},
            env=env,
        )
        if event_id is None:
            raise BridgeRequestError("write_event failed")
        if (
            event_type == "kol_inbound_reply"
            and campaign_id
            and isinstance(payload, dict)
        ):
            cal.append_pending_inbound_on_inbound_event(
                identity_id=identity_id,
                campaign_id=str(campaign_id),
                env=env,
                payload=payload,
                event_id=int(event_id),
            )

    def dispatch_context(
        self, *, identity_id: int, campaign_id: str, env: str,
    ) -> dict[str, Any]:
        if not cal.get_identity(identity_id):
            return {"error": "identity not found"}
        try:
            return build_dispatch_context_bundle(
                identity_id=identity_id, campaign_id=campaign_id, env=env,
            )
        except Exception as exc:  # noqa: BLE001
            raise BridgeRequestError(f"dispatch_context failed: {exc}") from exc

    def reply_chase_hint(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        message_id: str,
        thread_id: str | None,
        env: str,
    ) -> dict[str, Any]:
        try:
            return cal.reply_chase_hint(
                identity_id=identity_id,
                campaign_id=campaign_id,
                message_id=message_id,
                thread_id=thread_id,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            raise BridgeRequestError(f"reply_chase_hint failed: {exc}") from exc
