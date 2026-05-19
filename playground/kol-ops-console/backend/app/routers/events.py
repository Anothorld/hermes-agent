"""Real-time event multiplex: polls bridge ``/events/recent`` and fans out
to connected WebSocket clients.

The first version uses a small in-process poll loop (every 5s) rather than
hooking into Hermes' internal event bus. Trade-off: 5s freshness lag vs.
zero coupling. The poll watermark is ``latest_event_id``."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from ..bridge_client import BridgeClient
from ..config import get_settings
from ..deps import current_user, get_bridge
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
    return await bridge.recent_events(e, limit=limit)


@router.get("/escalations/open")
async def open_escalations(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _user: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> list[dict]:
    e = (env or get_settings().env).upper()
    return await bridge.list_open_escalations(e)


class _Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
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
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop(bridge))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

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
            # Discard inbound; this is a server-push channel.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.drop(ws)
