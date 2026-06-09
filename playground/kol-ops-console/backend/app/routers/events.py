"""Real-time event multiplex: polls bridge ``/events/recent`` and fans out
to connected WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge
from ..gateway_approval_watcher import watcher as approval_watcher
from ..perf_snapshot import perf
from ..security import decode_token

log = logging.getLogger(__name__)
router = APIRouter(tags=["events"])


@router.get("/events/recent")
async def recent_events(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    e = (env or get_settings().env).upper()
    try:
        return await bridge.recent_events(e, limit=limit)
    except BridgeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/escalations/open")
async def open_escalations(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> list[dict]:
    e = (env or get_settings().env).upper()
    try:
        return await bridge.list_open_escalations(e)
    except BridgeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


class EscalationNextActionBody(BaseModel):
    next_reply_type: str = Field(
        ...,
        pattern="^(product_pitch|brief_clarification|negotiation|content_followup|close_no_reply)$",
    )
    human_note: str | None = None
    env: str = Field(..., pattern="^(LIVE|TEST)$")


@router.post("/escalations/{escalation_id}/next-action")
async def escalation_next_action(
    escalation_id: int,
    body: EscalationNextActionBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(current_user)],
) -> dict:
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    try:
        return await bridge.choose_escalation_next_action(escalation_id, payload)
    except BridgeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


class _Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._approval_task: asyncio.Task | None = None
        self._last_id: int = 0

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)
            perf.ws_clients = len(self._clients)

    async def drop(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            perf.ws_clients = len(self._clients)

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        async with self._lock:
            for ws in self._clients:
                try:
                    await ws.send_text(text)
                except Exception:  # noqa: BLE001
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)
            perf.ws_clients = len(self._clients)

    async def start_poller(self, bridge: BridgeClient) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop(bridge))
        if self._approval_task is None:
            self._approval_task = asyncio.create_task(self._approval_relay_loop())

    async def stop(self) -> None:
        for attr in ("_task", "_approval_task"):
            task = getattr(self, attr)
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            setattr(self, attr, None)

    async def _poll_loop(self, bridge: BridgeClient) -> None:
        env = get_settings().env
        try:
            self._last_id = await bridge.latest_event_id(env)
        except Exception as exc:  # noqa: BLE001
            log.warning("bridge unreachable on poll-start: %s", exc)

        while True:
            try:
                await asyncio.sleep(5.0)
                events = await bridge.recent_events(
                    env, limit=200, since_id=self._last_id or None,
                )
                if not events:
                    continue
                events.sort(key=lambda e: int(e.get("id", 0)))
                self._last_id = int(events[-1].get("id", self._last_id))
                await self.broadcast({"type": "events", "items": events})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("event poll error: %s", exc)

    async def _approval_relay_loop(self) -> None:
        q = approval_watcher.subscribe()
        try:
            while True:
                ev = await q.get()
                await self.broadcast({"type": "gateway_approvals", "items": [ev]})
        except asyncio.CancelledError:
            raise
        finally:
            approval_watcher.unsubscribe(q)


hub = _Hub()


@router.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    token: str | None = Query(None),
) -> None:
    """Single channel for live updates. Auth: ?token=<jwt>."""
    if not token:
        await ws.close(code=4401)
        return
    try:
        decode_token(token)
    except Exception:  # noqa: BLE001
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.add(ws)
    await hub.start_poller(bridge)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.drop(ws)
