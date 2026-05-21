"""Thin httpx wrapper around the Hermes ``kol-ops-bridge`` plugin API (v2).

Mirrors the v2.4 endpoint surface (Phase A3). The legacy stage-driven
methods (push_contract_update / push_logistics_update / push_content_verdict
/ inject_inbound_reply / list_pending_drafts / get_draft / get_timeline /
add_alias / list_open_escalations / recent_events / latest_event_id /
list_identities / start_campaign / approve_shortlist / get_shortlist) have
been removed; the console UI must migrate to the new fact / goal / lane
surface in Phase C.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .config import get_settings


class BridgeError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"bridge {status}: {detail}")
        self.status = status
        self.detail = detail


class BridgeClient:
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.bridge_base.rstrip("/")
        self._headers = {"X-Bridge-Key": s.bridge_key} if s.bridge_key else {}
        self._client = httpx.AsyncClient(timeout=s.bridge_timeout_sec)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _req(
        self, method: str, path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base}{path}"
        try:
            r = await self._client.request(
                method, url, params=params, json=json, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise BridgeError(502, f"bridge unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BridgeError(r.status_code, r.text)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    # -------------------------------------------------------------- Health
    async def health(self) -> dict[str, Any]:
        return await self._req("GET", "/health")

    # ---------------------------------------------------------- Identities
    async def upsert_identity(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/identities", json=body)

    async def get_identity(self, identity_id: int) -> dict[str, Any]:
        return await self._req("GET", f"/identities/{identity_id}")

    async def get_relationship(self, identity_id: int) -> dict[str, Any]:
        return await self._req("GET", f"/identities/{identity_id}/relationship")

    async def get_reusable_facts(self, identity_id: int) -> dict[str, Any]:
        return await self._req(
            "GET", f"/identities/{identity_id}/relationship/reusable-facts"
        )

    async def get_goals(
        self, identity_id: int, campaign_id: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "GET", f"/identities/{identity_id}/goals",
            params={"campaign_id": campaign_id, "env": env},
        )

    async def archive_collab(
        self, identity_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/identities/{identity_id}/archive", json=body
        )

    # ----------------------------------------------------------- Campaigns
    async def upsert_campaign(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req("PUT", f"/campaigns/{campaign_id}", json=body)

    async def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        return await self._req("GET", f"/campaigns/{campaign_id}")

    async def list_candidates(
        self, campaign_id: str, env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        out = await self._req(
            "GET", f"/campaigns/{campaign_id}/candidates", params={"env": env}
        )
        return out.get("candidates", []) if isinstance(out, dict) else []

    async def upsert_candidate(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/campaigns/{campaign_id}/candidates", json=body
        )

    async def resolve_relationships(
        self, campaign_id: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/campaigns/{campaign_id}/candidates/resolve-relationships",
            params={"env": env},
        )

    async def select_candidates(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/campaigns/{campaign_id}/candidates/select", json=body
        )

    async def get_lanes(
        self, campaign_id: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "GET", f"/campaigns/{campaign_id}/lanes", params={"env": env}
        )

    # --------------------------------------------------------------- Facts
    async def write_facts(
        self, identity_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req("POST", f"/facts/{identity_id}", json=body)

    async def read_facts(
        self, identity_id: int,
        campaign_id: Optional[str] = None, env: str = "LIVE",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env}
        if campaign_id:
            params["campaign_id"] = campaign_id
        return await self._req("GET", f"/facts/{identity_id}", params=params)

    async def write_facts_multi(
        self, identity_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/facts/{identity_id}/multi", json=body
        )

    async def get_dispatch_context(
        self,
        identity_id: int,
        campaign_id: str,
        env: str = "LIVE",
    ) -> dict[str, Any]:
        return await self._req(
            "GET", f"/identities/{identity_id}/dispatch-context",
            params={"campaign_id": campaign_id, "env": env},
        )

    async def route_discovery(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/campaigns/{campaign_id}/candidates/route-discovery",
            json=body,
        )

    # ------------------------------------------------------------ Policies
    async def get_policy(
        self, scope: str, owner_user_id: Optional[int] = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if owner_user_id is not None:
            params["owner_user_id"] = owner_user_id
        return await self._req("GET", f"/policies/{scope}", params=params)

    async def put_policy(
        self, scope: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req("PUT", f"/policies/{scope}", json=body)

    async def policy_history(
        self,
        scope: str,
        owner_user_id: Optional[int] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if owner_user_id is not None:
            params["owner_user_id"] = owner_user_id
        return await self._req(
            "GET", f"/policies/{scope}/history", params=params
        )

    async def parsed_escalation_rules(self) -> dict[str, Any]:
        return await self._req("GET", "/policies/escalation_rules/parsed")

    # ----------------------------------------------------------- Approvals
    async def list_approvals(
        self, status: str = "pending", env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        out = await self._req(
            "GET", "/approvals", params={"status": status, "env": env}
        )
        return out.get("approvals", []) if isinstance(out, dict) else []

    async def approve(
        self, fact_path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/approvals/{fact_path}/approve", json=body
        )

    async def reject(
        self, fact_path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/approvals/{fact_path}/reject", json=body
        )

    # --------------------------------------------------------- Escalations
    async def list_escalations(
        self, state: Optional[str] = None, env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"env": env}
        if state:
            params["state"] = state
        out = await self._req("GET", "/escalations", params=params)
        return out.get("escalations", []) if isinstance(out, dict) else []

    async def open_escalation(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/escalations", json=body)

    async def resolve_escalation(
        self, escalation_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "PATCH", f"/escalations/{escalation_id}", json=body
        )

    # ---------------------------------------------------------------- Admin
    async def wipe_test(self) -> dict[str, Any]:
        return await self._req("POST", "/admin/wipe-test")
