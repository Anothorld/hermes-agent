"""Thin httpx wrapper around the Hermes ``kol-ops-bridge`` plugin API (v2).

Mirrors the v2.4 endpoint surface (Phase A3). The legacy stage-driven
methods (push_contract_update / push_logistics_update / push_content_verdict
/ inject_inbound_reply / list_pending_drafts / get_draft / add_alias /
latest_event_id / start_campaign) were retired in the Phase B cleanup as
the corresponding routers + UI were deleted.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .bridge_runtime import resolve_bridge_key
from .config import get_settings

_LEARNING_APPROVAL_FACT_PATHS = frozenset({
    "approval.style_learning_proposal",
    "approval.outcome_learning_proposal",
    "approval.discovery_learning_proposal",
})


class BridgeError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"bridge {status}: {detail}")
        self.status = status
        self.detail = detail


class BridgeClient:
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.bridge_base.rstrip("/")
        bridge_key = resolve_bridge_key(s)
        self._headers = {"X-Bridge-Key": bridge_key} if bridge_key else {}
        self._default_timeout = s.bridge_timeout_sec
        self._approve_timeout = s.bridge_approve_timeout_sec
        self._learning_timeout = s.bridge_learning_timeout_sec
        self._client = httpx.AsyncClient(
            timeout=self._default_timeout,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    def approval_timeout_for(self, fact_path: str) -> float:
        """Per-approval Bridge timeout (LLM merge and Gmail draft need longer waits)."""
        if fact_path in _LEARNING_APPROVAL_FACT_PATHS:
            return self._learning_timeout
        if fact_path == "approval.reply_draft":
            return self._approve_timeout
        return self._default_timeout

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _req(
        self, method: str, path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        retry: int = 0,
        operator_user_id: Optional[int] = None,
        timeout_sec: Optional[float] = None,
    ) -> Any:
        # ``retry`` retries ONLY on transient transport errors (httpx.HTTPError:
        # connect, timeout, read failures). HTTP 4xx/5xx response codes are
        # deterministic server-side decisions — retrying them is wasted work
        # and risks side effects on non-idempotent POSTs. Opt-in per-call so
        # only known-idempotent writes (PUT) and reads can ask for it.
        url = f"{self._base}{path}"
        headers = dict(self._headers)
        if operator_user_id is not None and operator_user_id > 0:
            headers["X-KOC-Operator-User-Id"] = str(operator_user_id)
        # Brief Hermes/bridge restarts and SQLite lock waits surface as
        # httpx transport errors. Idempotent GETs retry by default so the
        # console does not 502 on a single blip while the UI auto-refreshes.
        if retry == 0 and method.upper() in {"GET", "HEAD"}:
            retry = 2
        effective_timeout = (
            timeout_sec if timeout_sec is not None else self._default_timeout
        )
        attempts = retry + 1
        for i in range(attempts):
            try:
                r = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=effective_timeout,
                )
                break
            except httpx.HTTPError as exc:
                if i + 1 < attempts:
                    await asyncio.sleep(0.5)
                    continue
                hint = (
                    "（学习蒸馏/批准合并可能需 1–3 分钟，若超时请调大 KOC_BRIDGE_LEARNING_TIMEOUT_SEC）"
                    if effective_timeout > self._default_timeout
                    else (
                        "（批准学习提案需 LLM 合并，请确认 Console 已使用学习超时；"
                        "或调大 KOC_BRIDGE_LEARNING_TIMEOUT_SEC）"
                        if "/approvals/approval." in path
                        and "learning_proposal" in path
                        else ""
                    )
                )
                raise BridgeError(502, f"bridge unreachable: {exc}{hint}") from exc
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

    async def get_identity(
        self, identity_id: int, *, env: str = "LIVE",
    ) -> dict[str, Any]:
        return await self._req(
            "GET", f"/identities/{identity_id}", params={"env": env},
        )

    async def transfer_campaign(
        self, identity_id: int, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/identities/{identity_id}/transfer-campaign",
            json=body,
        )

    async def batch_outreach_touch(
        self, identity_ids: list[int], *, env: str = "LIVE",
    ) -> dict[str, Any]:
        if not identity_ids:
            return {"env": env, "items": {}}
        return await self._req(
            "GET",
            "/identities/outreach-touch",
            params={
                "env": env,
                "identity_ids": ",".join(str(i) for i in identity_ids),
            },
        )

    async def batch_internal_touch_count(
        self,
        *,
        env: str = "LIVE",
        identity_ids: list[int] | None = None,
        handles: list[str] | None = None,
    ) -> dict[str, Any]:
        ids = identity_ids or []
        hs = handles or []
        if not ids and not hs:
            return {"env": env, "items": {}}
        params: dict[str, str] = {"env": env}
        if ids:
            params["identity_ids"] = ",".join(str(i) for i in ids)
        if hs:
            params["handles"] = ",".join(hs)
        return await self._req(
            "GET",
            "/identities/internal-touch-count",
            params=params,
        )

    async def get_relationship(self, identity_id: int) -> dict[str, Any]:
        return await self._req("GET", f"/identities/{identity_id}/relationship")

    async def get_collab_history(self, identity_id: int) -> dict[str, Any]:
        return await self._req(
            "GET", f"/identities/{identity_id}/collab-history"
        )

    async def list_archived_kols(
        self,
        *,
        env: str = "LIVE",
        q: str | None = None,
        last_outcome: str | None = None,
        platform: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "limit": limit, "offset": offset}
        if q:
            params["q"] = q
        if last_outcome:
            params["last_outcome"] = last_outcome
        if platform:
            params["platform"] = platform
        return await self._req("GET", "/relationships", params=params)

    async def list_kol_registry(
        self,
        *,
        env: str = "LIVE",
        q: str | None = None,
        source: str = "all",
        sort: str = "ingested_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "env": env,
            "source": source,
            "sort": sort,
            "order": order,
            "limit": limit,
            "offset": offset,
        }
        if q:
            params["q"] = q
        return await self._req("GET", "/kol-registry", params=params)

    async def get_kol_registry_summary(
        self,
        *,
        env: str = "LIVE",
    ) -> dict[str, Any]:
        return await self._req("GET", "/kol-registry/summary", params={"env": env})

    async def get_kol_registry_summary_trend(
        self,
        *,
        env: str = "LIVE",
        bucket: str = "week",
        periods: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "bucket": bucket}
        if periods is not None:
            params["periods"] = periods
        return await self._req("GET", "/kol-registry/summary/trend", params=params)

    async def get_kol_registry_funnel(
        self,
        *,
        env: str = "LIVE",
        days: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "days": days}
        return await self._req("GET", "/kol-registry/funnel", params=params)

    async def get_kol_registry_funnel_trend(
        self,
        *,
        env: str = "LIVE",
        bucket: str = "week",
        periods: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "bucket": bucket}
        if periods is not None:
            params["periods"] = periods
        return await self._req("GET", "/kol-registry/funnel/trend", params=params)

    async def get_escalation_re_escalation_trend(
        self,
        *,
        env: str = "LIVE",
        bucket: str = "week",
        periods: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "bucket": bucket}
        if periods is not None:
            params["periods"] = periods
        return await self._req(
            "GET", "/escalations/re-escalation-trend", params=params,
        )

    async def get_escalation_re_escalation_window(
        self,
        *,
        env: str = "LIVE",
        days: int = 30,
    ) -> dict[str, Any]:
        return await self._req(
            "GET",
            "/escalations/re-escalation-window",
            params={"env": env, "days": days},
        )

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
        # Idempotent PUT — single retry covers brief network blips during
        # campaign launch so a transient outage at that exact moment doesn't
        # leave the campaign half-created (console row written, CAL row
        # empty). See campaigns.py launch flow.
        return await self._req(
            "PUT", f"/campaigns/{campaign_id}", json=body, retry=1,
        )

    async def get_campaign(
        self,
        campaign_id: str,
        *,
        env: str | None = None,
    ) -> dict[str, Any]:
        params = {"env": env} if env else None
        return await self._req(
            "GET",
            f"/campaigns/{campaign_id}",
            params=params,
        )

    async def parse_campaign_intent(
        self, text: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "POST", "/campaigns/parse",
            json={"text": text, "env": env},
        )

    async def parse_deliverables(
        self, text: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "POST", "/campaigns/parse-deliverables",
            json={"text": text, "env": env},
        )

    async def get_resolved_deliverables(
        self, campaign_id: str, *, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "GET",
            f"/campaigns/{campaign_id}/resolved-deliverables",
            params={"env": env},
        )

    async def append_campaign_facts_from_text(
        self, campaign_id: str, text: str, appended_by: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/campaigns/{campaign_id}/facts-from-text",
            json={"text": text, "appended_by": appended_by, "env": env},
        )

    async def list_candidates(
        self, campaign_id: str, env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        out = await self._req(
            "GET", f"/campaigns/{campaign_id}/candidates", params={"env": env}
        )
        return out.get("candidates", []) if isinstance(out, dict) else []

    async def list_candidate_handles(
        self, campaign_id: str, env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        """Candidates joined to identity handle/platform (one bridge round-trip)."""
        out = await self._req(
            "GET",
            f"/campaigns/{campaign_id}/candidate-handles",
            params={"env": env},
        )
        if isinstance(out, dict):
            items = out.get("items")
            if isinstance(items, list):
                return items
        return []

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

    async def set_candidate_status(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/campaigns/{campaign_id}/candidates/status", json=body
        )

    async def get_lanes(
        self, campaign_id: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        return await self._req(
            "GET", f"/campaigns/{campaign_id}/lanes", params={"env": env}
        )

    async def list_campaigns(
        self, env: Optional[str] = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if env is not None:
            params["env"] = env
        return await self._req("GET", "/campaigns", params=params)

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
        self,
        campaign_id: str,
        body: dict[str, Any],
        *,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/campaigns/{campaign_id}/candidates/route-discovery",
            json=body,
            timeout_sec=timeout_sec if timeout_sec is not None else self._approve_timeout,
        )

    # ------------------------------------------------------------ Policies
    async def get_policy(
        self,
        scope: str,
        owner_user_id: Optional[int] = None,
        env: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if owner_user_id is not None:
            params["owner_user_id"] = owner_user_id
        if env is not None:
            params["env"] = env
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
        env: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if owner_user_id is not None:
            params["owner_user_id"] = owner_user_id
        if env is not None:
            params["env"] = env
        return await self._req(
            "GET", f"/policies/{scope}/history", params=params
        )

    async def policy_version(
        self,
        scope: str,
        version: int,
        owner_user_id: Optional[int] = None,
        env: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if owner_user_id is not None:
            params["owner_user_id"] = owner_user_id
        if env is not None:
            params["env"] = env
        return await self._req(
            "GET", f"/policies/{scope}/version/{version}", params=params,
        )

    async def rollback_policy(
        self, scope: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._req("POST", f"/policies/{scope}/rollback", json=body)

    async def parsed_escalation_rules(self) -> dict[str, Any]:
        return await self._req("GET", "/policies/escalation_rules/parsed")

    # ----------------------------------------------------------- Approvals
    async def list_approvals(
        self,
        status: str = "pending",
        env: str = "LIVE",
        *,
        identity_id: Optional[int] = None,
        campaign_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": status, "env": env}
        if identity_id is not None:
            params["identity_id"] = identity_id
        if campaign_id:
            params["campaign_id"] = campaign_id
        out = await self._req("GET", "/approvals", params=params)
        return out.get("approvals", []) if isinstance(out, dict) else []

    async def batch_facts_subset(
        self,
        *,
        campaign_id: str,
        identity_ids: list[int],
        env: str,
        fact_keys: list[str],
    ) -> dict[str, dict[str, Any]]:
        out = await self._req(
            "POST",
            "/facts/batch-subset",
            json={
                "campaign_id": campaign_id,
                "identity_ids": identity_ids,
                "env": env,
                "fact_keys": fact_keys,
            },
        )
        raw = out.get("by_identity") if isinstance(out, dict) else {}
        if not isinstance(raw, dict):
            return {}
        parsed: dict[int, dict[str, Any]] = {}
        for key, facts in raw.items():
            try:
                iid = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(facts, dict):
                parsed[iid] = facts
        return parsed

    async def batch_identity_briefs(
        self, identity_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        if not identity_ids:
            return {}
        out = await self._req(
            "POST",
            "/identities/briefs",
            json={"identity_ids": identity_ids},
        )
        raw = out.get("identities") if isinstance(out, dict) else {}
        if not isinstance(raw, dict):
            return {}
        parsed: dict[int, dict[str, Any]] = {}
        for key, brief in raw.items():
            try:
                iid = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(brief, dict):
                parsed[iid] = brief
        return parsed

    async def approve(
        self,
        fact_path: str,
        body: dict[str, Any],
        *,
        operator_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/approvals/{fact_path}/approve",
            json=body,
            operator_user_id=operator_user_id,
            timeout_sec=self.approval_timeout_for(fact_path),
        )

    async def reject(
        self,
        fact_path: str,
        body: dict[str, Any],
        *,
        operator_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/approvals/{fact_path}/reject",
            json=body,
            operator_user_id=operator_user_id,
            timeout_sec=self.approval_timeout_for(fact_path),
        )

    async def reconcile_sent(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/gmail/reconcile-sent", json=body)

    async def inbound_poller_status(self) -> dict[str, Any]:
        payload = await self._req("GET", "/gmail/inbound-poller/status")
        return self._unwrap_inbound_poller(payload)

    async def inbound_poller_start(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = await self._req("POST", "/gmail/inbound-poller/start", json=body)
        return self._unwrap_inbound_poller(payload)

    async def inbound_poller_stop(self) -> dict[str, Any]:
        payload = await self._req("POST", "/gmail/inbound-poller/stop")
        return self._unwrap_inbound_poller(payload)

    async def inbound_poller_restart(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = await self._req("POST", "/gmail/inbound-poller/restart", json=body)
        return self._unwrap_inbound_poller(payload)

    @staticmethod
    def _unwrap_inbound_poller(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and payload.get("ok"):
            return {k: v for k, v in payload.items() if k != "ok"}
        return payload if isinstance(payload, dict) else {}

    async def takeover_mailbox(
        self,
        identity_id: int,
        body: dict[str, Any],
        *,
        operator_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/identities/{identity_id}/mailbox/takeover",
            json=body,
            operator_user_id=operator_user_id,
        )

    async def promote_strategy(self, body: dict[str, Any]) -> dict[str, Any]:
        """Preview (dry_run) or apply promotion of a reply_strategy goal."""
        return await self._req("POST", "/learning/promote-strategy", json=body)

    # ------------------------------------- Discovery decision learning
    async def record_shortlist_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        """Persist operator shortlist decisions (approve/remove/transfer) as learning events."""
        return await self._req("POST", "/learning/shortlist-decision", json=body)

    async def list_discovery_tags(
        self, *, action: Optional[str] = None, status: str = "active",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"status": status}
        if action:
            params["action"] = action
        return await self._req("GET", "/learning/discovery-tags", params=params)

    async def decide_discovery_tag(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/learning/discovery-tags/decide", json=body)

    async def discovery_feedback_requirements(
        self, *, sku: Optional[str], env: str = "LIVE",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env}
        if sku:
            params["sku"] = sku
        return await self._req(
            "GET", "/learning/discovery-feedback-requirements", params=params,
        )

    async def list_shortlist_decision_events(
        self,
        *,
        env: str = "LIVE",
        sku: Optional[str] = None,
        category: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"env": env, "limit": limit}
        if sku:
            params["sku"] = sku
        if category:
            params["category"] = category
        if action:
            params["action"] = action
        return await self._req(
            "GET", "/learning/shortlist-decision-events", params=params,
        )

    async def get_discovery_criteria(
        self, *, sku: str, env: str = "LIVE", max_chars: int = 4000,
    ) -> dict[str, Any]:
        return await self._req(
            "GET",
            "/learning/discovery-criteria",
            params={"sku": sku, "env": env, "max_chars": max_chars},
        )

    async def list_pending_discovery_proposals(
        self, *, env: str = "LIVE",
    ) -> dict[str, Any]:
        return await self._req(
            "GET", "/learning/pending-discovery-proposals", params={"env": env},
        )

    async def list_product_categories(self) -> dict[str, Any]:
        return await self._req("GET", "/learning/product-categories")

    async def put_product_category(
        self, sku: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._req(
            "PUT", f"/learning/product-categories/{sku}", json=body,
        )

    # --------------------------------------------------------- Escalations
    async def list_escalations(
        self,
        state: Optional[str] = None,
        env: str = "LIVE",
        *,
        identity_id: Optional[int] = None,
        campaign_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"env": env}
        if state:
            params["state"] = state
        if identity_id is not None:
            params["identity_id"] = identity_id
        if campaign_id:
            params["campaign_id"] = campaign_id
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

    async def sync_escalation_pending_inbounds(
        self, escalation_id: int,
    ) -> dict[str, Any]:
        return await self._req(
            "POST", f"/escalations/{escalation_id}/sync-pending-inbounds",
        )

    # --------------------------------------------------------------- Events
    async def recent_events(
        self,
        env: str = "LIVE",
        limit: int = 200,
        campaign_id: str | None = None,
        since_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Recent conversation events across all identities (reverse-chrono)."""
        params: dict[str, Any] = {"env": env, "limit": limit}
        if campaign_id:
            params["campaign_id"] = campaign_id
        if since_id is not None:
            params["since_id"] = since_id
        out = await self._req("GET", "/events/recent", params=params)
        return list(out.get("events") or [])

    async def latest_event_id(self, env: str = "LIVE") -> int:
        """High-water mark for incremental WS polling."""
        events = await self.recent_events(env, limit=1)
        if not events:
            return 0
        return int(events[0].get("id") or 0)

    async def write_event(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._req("POST", "/events", json=body)

    async def get_timeline(
        self,
        identity_id: int,
        env: str = "LIVE",
        campaign_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Per-identity event timeline (reverse-chrono)."""
        params: dict[str, Any] = {"env": env, "limit": limit}
        if campaign_id:
            params["campaign_id"] = campaign_id
        out = await self._req(
            "GET", f"/identities/{identity_id}/timeline", params=params
        )
        return list(out.get("events") or [])

    async def get_email_conversation(
        self,
        identity_id: int,
        campaign_id: str,
        env: str = "LIVE",
        *,
        operator_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Gmail sent/received messages (no drafts) for communication history UI."""
        return await self._req(
            "GET",
            f"/identities/{identity_id}/email-conversation",
            params={"campaign_id": campaign_id, "env": env},
            operator_user_id=operator_user_id,
        )

    # ---------------------------------------------- Drafts / replies (dead)
    # Phase A retired the kol_drafts / kol_replies persistence; the related
    # routers + UI were deleted in Phase B cleanup, so no bridge wrappers
    # are needed here.

    # ------------------------------------------- Shortlist
    async def get_shortlist(
        self, campaign_id: str, env: str = "LIVE"
    ) -> dict[str, Any]:
        # Mirrors bridge candidates endpoint shape.
        out = await self.list_candidates(campaign_id, env=env)
        return {"campaign_id": campaign_id, "candidates": out}

    async def approve_shortlist(
        self, campaign_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.select_candidates(campaign_id, body)

    # ------------------------------------------------- Open escalations
    async def list_open_escalations(
        self, env: str = "LIVE"
    ) -> list[dict[str, Any]]:
        """Return ``state='awaiting_answer'`` escalations for the env.

        Maps bridge's ``identity_id`` -> ``kol_identity_id`` for the
        web console which uses the latter naming consistently.
        """
        rows = await self.list_escalations(env=env, state="awaiting_answer")
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if "identity_id" in d and "kol_identity_id" not in d:
                d["kol_identity_id"] = d["identity_id"]
            # Surface the open timestamp under a uniform ``ts`` key the
            # UI already renders.
            if "ts" not in d and "created_at" in d:
                d["ts"] = d["created_at"]
            out.append(d)
        return out

    async def choose_escalation_next_action(
        self, escalation_id: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Operator picks the next reply type for an open escalation.

        Bridge has no dedicated endpoint yet; we reuse
        ``PATCH /escalations/{id}`` with a structured ``decision`` so the
        bridge's escalation resumer can pick the chosen action from
        ``resume_context`` on the next dispatch.  ``human_note`` is
        forwarded as ``operator_answer``.
        """
        next_type = body.get("next_reply_type") or "unspecified"
        actor = body.get("actor") or "web:unknown"
        payload = {
            "decision": f"next_action:{next_type}",
            "decided_by": actor,
            "operator_answer": body.get("human_note"),
            "final_state": "answered",
        }
        return await self.resolve_escalation(escalation_id, payload)

    # ------------------------------------------------------------- Contracts
    async def get_contract_preview(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        env: str = "LIVE",
        attachment_path: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"campaign_id": campaign_id, "env": env}
        if attachment_path:
            params["attachment_path"] = attachment_path
        return await self._req(
            "GET",
            f"/identities/{identity_id}/contract-preview",
            params=params,
        )

    async def download_contract(
        self,
        *,
        identity_id: int,
        campaign_id: str,
        env: str = "LIVE",
        attachment_path: str | None = None,
    ) -> httpx.Response:
        params: dict[str, str] = {"campaign_id": campaign_id, "env": env}
        if attachment_path:
            params["attachment_path"] = attachment_path
        url = f"{self._base}/identities/{identity_id}/contract-download"
        headers = dict(self._headers)
        r = await self._client.get(url, params=params, headers=headers, timeout=self._default_timeout)
        if r.status_code >= 400:
            raise BridgeError(r.status_code, r.text)
        return r

    # ---------------------------------------------------------------- Admin
    async def wipe_test(self) -> dict[str, Any]:
        return await self._req("POST", "/admin/wipe-test")
