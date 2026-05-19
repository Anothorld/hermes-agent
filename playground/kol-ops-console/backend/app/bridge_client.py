"""Thin httpx wrapper around the Hermes ``kol-ops-bridge`` plugin API."""

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
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base}{path}"
        r = await self._client.request(method, url, params=params, json=json, headers=self._headers)
        if r.status_code >= 400:
            raise BridgeError(r.status_code, r.text)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    # --- Read passthroughs ---------------------------------------------------
    async def health(self) -> dict[str, Any]:
        return await self._req("GET", "/health")

    async def list_identities(self, env: str) -> list[dict[str, Any]]:
        return await self._req("GET", "/identities", params={"env": env})

    async def get_identity(self, identity_id: int) -> dict[str, Any]:
        return await self._req("GET", f"/identities/{identity_id}")

    async def get_timeline(self, identity_id: int, env: str) -> dict[str, Any]:
        return await self._req("GET", f"/identities/{identity_id}/timeline",
                               params={"env": env})

    async def list_pending_drafts(self, env: str) -> list[dict[str, Any]]:
        return await self._req("GET", "/drafts/pending", params={"env": env})

    async def get_draft(self, draft_id: str, env: str) -> dict[str, Any]:
        return await self._req("GET", f"/drafts/{draft_id}", params={"env": env})

    async def list_open_escalations(self, env: str) -> list[dict[str, Any]]:
        return await self._req("GET", "/escalations/open", params={"env": env})

    async def recent_events(self, env: str, limit: int = 100) -> list[dict[str, Any]]:
        return await self._req("GET", "/events/recent", params={"env": env, "limit": limit})

    async def latest_event_id(self, env: str) -> int:
        out = await self._req("GET", "/events/latest-id", params={"env": env})
        return int(out.get("latest_event_id", 0)) if isinstance(out, dict) else int(out)

    # --- Writes --------------------------------------------------------------
    async def push_contract_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/contract/update", json=payload)

    async def push_logistics_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/logistics/update", json=payload)

    async def push_content_verdict(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/content/verdict", json=payload)

    async def resolve_escalation(self, escalation_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/escalations/{escalation_id}/resolve", json=payload)

    async def add_alias(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/identities/aliases", json=payload)

    async def start_campaign(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", f"/campaigns/{campaign_id}/start", json=payload)

    async def wipe_test(self) -> dict[str, Any]:
        return await self._req("POST", "/admin/wipe-test")
