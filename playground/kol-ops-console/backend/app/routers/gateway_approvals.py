"""HTTP surface for the gateway-approval watcher.

The watcher (``app.gateway_approval_watcher.watcher``) keeps a live
snapshot of every API-triggered run that is currently blocked on a
dangerous-command approval. This router exposes:

* ``GET /gateway-approvals``               — current snapshot + seq
* ``POST /gateway-approvals/{run_id}/resolve`` — operator's choice,
  proxied to ``POST /v1/runs/{run_id}/approval`` on the upstream
  hermes gateway.

The websocket fan-out lives in ``routers/events.py``; subscribers
receive ``{type:"gateway_approvals", items:[...]}`` frames with the
same payload shape as the snapshot entries.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..deps import current_user, get_conn, get_gateway, require_role
from ..gateway_approval_watcher import watcher
from ..gateway_client import GatewayClient, GatewayError

router = APIRouter(prefix="/gateway-approvals", tags=["gateway-approvals"])


ApprovalChoice = Literal["once", "session", "always", "deny"]


class ResolveBody(BaseModel):
    choice: ApprovalChoice
    note: Optional[str] = Field(default=None, max_length=1000)


@router.get("")
async def list_gateway_approvals(
    _user: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    items, seq = watcher.snapshot()
    return {"approvals": items, "seq": seq}


@router.post("/{run_id}/resolve")
async def resolve_gateway_approval(
    run_id: str,
    body: ResolveBody,
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """Proxy the operator's decision to the upstream gateway.

    We do NOT clear the watcher's ``_pending`` entry here. The gateway's
    own SSE stream will fire ``approval.responded`` once the agent's
    blocked thread is released; the watcher consumes that and broadcasts
    the cleared event. Clearing eagerly would risk a stale state if the
    upstream POST appears to succeed but the actual resume races (e.g.
    gateway accepted the choice but the agent died).
    """
    try:
        upstream = await gateway.resolve_approval(run_id, choice=body.choice)
    except GatewayError as exc:
        # 409 from the gateway means "no pending approval" — return it as
        # 409 here too so the FE can drop the entry silently (the
        # approval was resolved or cleared just before this click).
        raise HTTPException(
            status_code=exc.status if exc.status in (404, 409) else status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="gateway_approval.resolve",
        target=run_id,
        payload={"choice": body.choice, "note": body.note},
    )
    return upstream
