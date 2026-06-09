"""Real-time event multiplex: polls bridge ``/events/recent`` and fans out
to connected WebSocket clients.

The first version uses a small in-process poll loop (every 5s) rather than
hooking into Hermes' internal event bus. Trade-off: 5s freshness lag vs.
zero coupling. The poll watermark is ``latest_event_id``."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge
from ..gateway_approval_watcher import watcher as approval_watcher
from ..security import decode_token

log = logging.getLogger(__name__)
router = APIRouter(tags=["events"])


# ---------------------------------------------------------------------------
# Read passthroughs (recent events + open escalations)
# ---------------------------------------------------------------------------


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

    async def drop(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

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
                events = await bridge.recent_events(env, limit=200)
                fresh = [e for e in events if int(e.get("id", 0)) > self._last_id]
                if not fresh:
                    continue
                fresh.sort(key=lambda e: int(e["id"]))
                self._last_id = int(fresh[-1]["id"])
                await self.broadcast({"type": "events", "items": fresh})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("event poll error: %s", exc)

    async def _approval_relay_loop(self) -> None:
        """Fan watcher events out to websocket clients.

        Frame type is ``"gateway_approvals"`` so the frontend hook can
        discriminate against the existing ``"events"`` (bridge) feed.
        """
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

# #region agent log
_DEBUG_LOG = "/Users/arnold/agent_prj/.cursor/debug-bba44f.log"


def _agent_dbg(*, location: str, message: str, data: dict, hypothesis_id: str) -> None:
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sessionId": "bba44f",
                        "timestamp": int(time.time() * 1000),
                        "location": location,
                        "message": message,
                        "data": data,
                        "hypothesisId": hypothesis_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


# #endregion


@router.websocket("/ws")
async def ws_endpoint(
    ws: WebSocket,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    token: str | None = Query(None),
) -> None:
    """Single channel for live updates. Auth: ?token=<jwt>."""
    if not token:
        # #region agent log
        _agent_dbg(
            location="events.py:ws_endpoint",
            message="ws rejected no token",
            data={},
            hypothesis_id="E",
        )
        # #endregion
        await ws.close(code=4401)
        return
    try:
        decode_token(token)
    except Exception:  # noqa: BLE001
        # #region agent log
        _agent_dbg(
            location="events.py:ws_endpoint",
            message="ws rejected bad token",
            data={},
            hypothesis_id="E",
        )
        # #endregion
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.add(ws)
    # #region agent log
    _agent_dbg(
        location="events.py:ws_endpoint",
        message="ws accepted",
        data={"client_count": len(hub._clients)},
        hypothesis_id="A",
    )
    # #endregion
    await hub.start_poller(bridge)
    disconnect_code: int | None = None
    try:
        while True:
            # Discard inbound; this is a server-push channel.
            await ws.receive_text()
    except WebSocketDisconnect as exc:
        disconnect_code = exc.code
    finally:
        await hub.drop(ws)
        # #region agent log
        _agent_dbg(
            location="events.py:ws_endpoint",
            message="ws disconnected",
            data={
                "disconnect_code": disconnect_code,
                "client_count": len(hub._clients),
            },
            hypothesis_id="A",
        )
        # #endregion
