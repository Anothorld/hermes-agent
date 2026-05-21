"""Start campaigns (proxy to the Hermes Gateway ``/v1/runs`` API).

Phase B wired the launch path through the gateway directly so the bridge
stays purely a deterministic CAL writer/reader.  See
:meth:`gateway_client.GatewayClient.start_run` for the underlying HTTP
contract.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Structured system prompt for the launch agent run.  Listed values are
# the contract that downstream skills (kol-campaign-intake,
# kol-discovery-to-outreach-router) will read out of the brief.
_LAUNCH_INSTRUCTIONS = (
    "You are launching a KOL outreach campaign via the web console.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    "- kol-ops-bridge base URL: http://127.0.0.1:8080/api/plugins/kol-ops-bridge\n"
    "  (override with HERMES_KOL_OPS_BRIDGE_BASE if needed)\n"
    "- Bridge auth header: X-Bridge-Key: $HERMES_KOL_OPS_BRIDGE_KEY\n"
    "  (already in your environment; never echo the value)\n"
    "- Working directory is /home/pc; the project lives at\n"
    "  /home/pc/agent_prj/hermes-agent. ALWAYS use absolute paths.\n"
    "- ALL CAL writes/reads go through the deterministic CLI; never\n"
    "  hand-craft curl/PUT/POST. Single entry point:\n"
    "    python /home/pc/agent_prj/hermes-agent/plugins/kol-ops-bridge/\n"
    "      scripts/kol_bridge_tool.py <cmd> --env <env> [--campaign-id <id>] ...\n"
    "  Run with `--help` once to enumerate subcommands. Key ones:\n"
    "    upsert-campaign, get-campaign, add-candidate, list-candidates,\n"
    "    select-candidates, resolve-relationships, route-discovery,\n"
    "    upsert-identity, get-identity, get-timeline, archive-identity,\n"
    "    list-events, write-event, write-facts, write-facts-multi,\n"
    "    get-goals, open-escalation, resolve-escalation, get-policy,\n"
    "    set-policy, get-parsed-escalation-rules.\n"
    "  Large JSON bodies: pass `--json @/tmp/body.json`.\n"
    "\n"
    "## Pipeline (run in order, do NOT skip)\n"
    "1. `skill_view(name='kol-campaign-intake')` then parse the brief\n"
    "   below and persist campaign_config via\n"
    "   `kol_bridge_tool.py upsert-campaign --env <env> --campaign-id <id>\n"
    "     --json @/tmp/campaign.json` (atomic).\n"
    "   Honor every key in `# campaign_config` verbatim, including\n"
    "   `discovery_target_count` and `product_pitch`.\n"
    "2. `skill_view(name='kol-outreach-orchestrator-flow')` to confirm\n"
    "   the master playbook; for a fresh launch the next step is\n"
    "   kol-discovery-to-outreach-router -> Instagram KOL discovery.\n"
    "3. `skill_view(name='instagram-kol-discovery')` and then EXECUTE\n"
    "   discovery using the built-in BrowserUse tools — `browser_navigate`,\n"
    "   `browser_snapshot`, `browser_get_images`, `browser_click`,\n"
    "   `browser_type`, `vision_analyze`. The discovery skill is NOT\n"
    "   optional and must produce at least `discovery_target_count`\n"
    "   raw candidates (which is set to 2-4x `headcount_target`).\n"
    "   Do NOT use the `mcp_chrome_devtools_*` family — those are flaky\n"
    "   here; stick to `browser_*`.\n"
    "4. Persist each candidate via the CLI\n"
    "   (`kol_bridge_tool.py add-candidate --env <env> --campaign-id <id>\n"
    "     --primary-handle <h> --source discovery:<channel>\n"
    "     --discovery-score <0..1>` per identity, then\n"
    "   `resolve-relationships`). After every batch, call\n"
    "   `list-candidates` once to confirm persistence.\n"
    "5. STOP after raw candidates are persisted and relationships\n"
    "   resolved. Do NOT shortlist, draft emails, or send anything —\n"
    "   the operator reviews the pool in the web console and explicitly\n"
    "   approves before any outreach goes out.\n"
    "\n"
    "## Environment safety\n"
    "- If `mode: TEST`, route every outbound email to `test_mode_to`.\n"
    "- If `mode: LIVE`, real addresses may be used but you must still\n"
    "  wait for operator approval before sending.\n"
    "- All CLI invocations MUST pass `--env <TEST|LIVE>` matching the\n"
    "  brief; never rely on a default.\n"
    "\n"
    "## Failure handling\n"
    "- If the bridge returns 401, the X-Bridge-Key header is missing —\n"
    "  re-issue via the CLI (which reads HERMES_KOL_OPS_BRIDGE_KEY) or\n"
    "  add `--bridge-key $HERMES_KOL_OPS_BRIDGE_KEY` explicitly.\n"
    "- If a path returns 404, you almost certainly forgot the\n"
    "  `/api/plugins/kol-ops-bridge/` prefix or used port 8765 (console)\n"
    "  instead of 8080 (bridge).\n"
    "- On 3 consecutive identical failures, STOP and open an escalation\n"
    "  via `kol_bridge_tool.py open-escalation` rather than looping.\n"
)


def _compose_brief(campaign_id: str, product: sqlite3.Row, body: "StartCampaignBody") -> str:
    tags = json.loads(product["tags_json"] or "[]")
    sku_ref = product["url"] or product["sku"]
    discovery_target = body.discovery_target_count or max(
        body.headcount_target * 3, body.headcount_target + 5
    )
    lines = [
        "# campaign_config",
        f"campaign_id: {campaign_id}",
        f"product_sku: {product['sku']}",
        f"product_name: {product['name']}",
        f"mode: {body.env}",
        "sku_whitelist:",
        f"  - {sku_ref}",
        f"budget_total: {body.budget_total:g}",
        f"budget_per_kol: {body.budget_per_kol:g}",
        f"absolute_floor: {body.absolute_floor:g}",
        f"headcount_target: {body.headcount_target}",
        f"discovery_target_count: {discovery_target}",
        f"test_mode_to: {body.test_mode_to}",
        "triggered_by: web",
    ]
    if product["url"]:
        lines.append(f"product_url: {product['url']}")
    if tags:
        lines.append(f"product_tags: {', '.join(tags)}")
    if product["notes"]:
        lines.extend(["product_notes:", product["notes"]])

    pitch = (body.product_pitch_md or "").strip()
    if pitch:
        lines.extend([
            "",
            "# product_pitch (markdown - feed to KOL discovery + outreach skills)",
            pitch,
        ])

    extra = (body.brief_extra or "").strip()
    if extra:
        lines.extend([
            "",
            "# operator_brief (supplied via web console)",
            extra,
        ])
    return "\n".join(lines)


# Cap operator-supplied free-text fields to keep upstream token cost
# predictable.  16k chars ~ 4k tokens; 64k chars ~ 16k tokens.
_MAX_BRIEF_EXTRA = 16_000
_MAX_PRODUCT_PITCH = 64_000


class StartCampaignBody(BaseModel):
    product_sku: str
    budget_per_kol: float = Field(gt=0)
    absolute_floor: float = Field(gt=0)
    budget_total: float = Field(gt=0)
    headcount_target: int = Field(ge=1, le=200)
    test_mode_to: str
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    product_pitch_md: str | None = Field(
        default=None,
        max_length=_MAX_PRODUCT_PITCH,
        description=(
            "Operator-supplied product selling-points (markdown or plain text).\n"
            "Required for KOL discovery quality - the discovery skill uses this\n"
            "to derive search keywords, audience fit and pitch hooks."
        ),
    )
    discovery_target_count: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        description=(
            "How many raw KOL candidates discovery should aim for. "
            "Defaults to max(headcount_target * 3, headcount_target + 5) "
            "so the operator can review a 2-4x funnel before shortlisting."
        ),
    )
    brief_extra: str | None = Field(
        default=None,
        max_length=_MAX_BRIEF_EXTRA,
        description="Optional free-form operator notes / constraints.",
    )


@router.post("/{campaign_id}/start")
async def start(
    campaign_id: str,
    body: StartCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    force: bool = Query(False, description="Override duplicate-campaign guard."),
) -> dict:
    product = conn.execute(
        "SELECT sku, name, url, tags_json, notes FROM products WHERE sku=?",
        (body.product_sku,),
    ).fetchone()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")

    # Anti-duplicate guard. The bridge does not currently dedupe, so the
    # console owns this check. ``force=true`` lets the operator re-fire
    # intentionally (e.g. after a 402 failure) without dropping the audit row.
    if not force:
        existing = conn.execute(
            "SELECT run_id, status FROM product_campaigns WHERE campaign_id=? AND env=?",
            (campaign_id, body.env),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"campaign already started (run_id={existing['run_id']}, "
                f"status={existing['status']}); pass ?force=true to retry",
            )
        active = conn.execute(
            "SELECT campaign_id, run_id FROM product_campaigns "
            "WHERE sku=? AND env=? AND status='running' LIMIT 1",
            (product["sku"], body.env),
        ).fetchone()
        if active is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"sku already has a running campaign "
                f"(campaign_id={active['campaign_id']}, run_id={active['run_id']}); "
                "close it first or pass ?force=true",
            )

    payload = body.model_dump()
    sku_ref = product["url"] or product["sku"]
    payload["product_name"] = product["name"]
    payload["product_url"] = product["url"]
    payload["sku_whitelist"] = [sku_ref]
    brief_text = _compose_brief(campaign_id, product, body)
    payload["brief"] = brief_text
    payload["triggered_by"] = "web"
    payload["actor"] = f"web:{user['email']}"

    # Seed campaign metadata in the bridge first so downstream skills can
    # find the campaign row before discovery starts writing candidates.
    try:
        await bridge.upsert_campaign(
            campaign_id,
            {
                "campaign_id": campaign_id,
                "title": product["name"],
                "sku_whitelist": [sku_ref],
                "paid_ceiling": body.budget_per_kol,
                "contract_required": True,
            },
        )
    except BridgeError as exc:
        # Non-fatal: the agent's intake step will retry the upsert.  We
        # log it on the campaign row via audit only.
        write_audit(
            conn,
            actor_user_id=user["id"],
            action="campaign.upsert_warning",
            target=campaign_id,
            payload={"error": str(exc)},
        )

    try:
        out = await gateway.start_run(
            input=brief_text,
            instructions=_LAUNCH_INSTRUCTIONS,
            session_id=f"kol-campaign:{body.env}:{campaign_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    run_id = out.get("run_id") if isinstance(out, dict) else None
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO product_campaigns
             (sku, campaign_id, env, run_id, started_at, started_by_user_id, status)
           VALUES (?,?,?,?,?,?, 'running')
           ON CONFLICT(campaign_id, env) DO UPDATE SET
             run_id=excluded.run_id,
             started_at=excluded.started_at,
             started_by_user_id=excluded.started_by_user_id,
             status='running'""",
        (product["sku"], campaign_id, body.env, run_id, now, user["id"]),
    )
    write_audit(conn, actor_user_id=user["id"], action="campaign.start",
                target=campaign_id, payload=payload)
    return out


class CloseCampaignBody(BaseModel):
    status: str = Field(default="closed", pattern="^(closed|cancelled)$")


@router.post("/{campaign_id}/close")
async def close(
    campaign_id: str,
    body: CloseCampaignBody,
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    env: str = Query(..., pattern="^(LIVE|TEST)$"),
) -> dict:
    """Best-effort stop the gateway run, then close the console row.

    ``Mark closed`` started life as a console-only state flip, but in practice
    operators expect it to stop the backing agent run as well. We therefore
    try ``POST /v1/runs/{id}/stop`` when a ``run_id`` is known, but never let
    gateway errors prevent the row from being closed in the console.
    """
    row = conn.execute(
        "SELECT status, run_id FROM product_campaigns WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not tracked")

    stop_result: dict[str, object] | None = None
    run_id = row["run_id"]
    if run_id:
        try:
            stop_ack = await gateway.stop_run(run_id)
            stop_result = {
                "requested": True,
                "run_id": run_id,
                "gateway_status": stop_ack.get("status") if isinstance(stop_ack, dict) else None,
            }
        except GatewayError as exc:
            stop_result = {
                "requested": False,
                "run_id": run_id,
                "error": str(exc),
            }

    conn.execute(
        "UPDATE product_campaigns SET status=? WHERE campaign_id=? AND env=?",
        (body.status, campaign_id, env),
    )
    write_audit(conn, actor_user_id=user["id"], action="campaign.close",
                target=campaign_id, payload={
                    "env": env,
                    "status": body.status,
                    "run_id": run_id,
                    "stop_result": stop_result,
                })
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "env": env,
        "status": body.status,
        "run_id": run_id,
        "stop_result": stop_result,
    }


class ApproveShortlistBody(BaseModel):
    """Body for the operator's shortlist approval click."""

    selected_handles: list[str] = Field(default_factory=list)
    note: str | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.get("/{campaign_id}/shortlist")
async def get_shortlist(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
) -> dict:
    """Return the agent's latest shortlist_ready payload (candidates + scores).

    Used by the per-product review panel so operators can pick a subset
    before approval.
    """
    try:
        return await bridge.get_shortlist(campaign_id, env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.post("/{campaign_id}/approve-shortlist")
async def approve_shortlist(
    campaign_id: str,
    body: ApproveShortlistBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Forward shortlist approval to the bridge; record audit row."""
    payload = body.model_dump()
    payload["actor"] = f"web:{user['email']}"
    payload["triggered_by"] = "web"
    try:
        out = await bridge.approve_shortlist(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    new_run_id = out.get("run_id") if isinstance(out, dict) else None
    if new_run_id:
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running' "
            "WHERE campaign_id=? AND env=?",
            (new_run_id, campaign_id, body.env),
        )
    write_audit(conn, actor_user_id=user["id"], action="campaign.approve_shortlist",
                target=campaign_id, payload=payload)
    return out


# Goal status values we treat as "the lane's active column". Anything else
# (inactive / completed) falls back to None so the UI bucket stays correct.
_ACTIVE_GOAL_STATES = {"in_progress", "blocked", "awaiting_human"}


def _pick_active_per_lane(lanes: dict) -> dict:
    """Bridge returns ``{lane: [goal_state,...]}``; the console renders a
    single ``goal`` per lane. Pick the first non-inactive goal; otherwise
    the last (most advanced) goal so the column is never empty.
    """
    out: dict = {"commerce": None, "fulfillment": None, "publish": None, "meta": None}
    for lane, states in (lanes or {}).items():
        if not states:
            continue
        active = next((s for s in states if s.get("status") in _ACTIVE_GOAL_STATES), None)
        chosen = active or states[-1]
        out[lane] = {
            "goal": chosen.get("goal"),
            "state": chosen.get("status") or "inactive",
            "missing_facts": chosen.get("missing_facts") or [],
            "blocked_reason": chosen.get("blocking_escalation_id") or None,
        }
    return out


@router.get("/{campaign_id}/lanes")
async def lanes(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("LIVE", pattern="^(LIVE|TEST)$"),
) -> dict:
    """Kanban data feed: per-identity lane snapshot + top-of-page counts.

    Returns ``{campaign_id, lanes: LaneSnapshot[], counts:
    {pending_approvals, open_escalations}}``. Bridge errors → 502.
    """
    try:
        raw = await bridge.get_lanes(campaign_id, env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    items_out = []
    for it in raw.get("items", []):
        items_out.append({
            "identity_id": it["identity_id"],
            "handle": it.get("handle") or f"id{it['identity_id']}",
            "candidate_status": it.get("candidate_status"),
            "relationship_status": it.get("relationship_status"),
            "repeat_count": it.get("repeat_count") or 0,
            "last_outcome": it.get("last_outcome"),
            "archived": bool(it.get("archived")),
            "goals": _pick_active_per_lane(it.get("lanes") or {}),
        })
    return {
        "campaign_id": campaign_id,
        "lanes": items_out,
        "counts": raw.get("counts") or {"pending_approvals": 0, "open_escalations": 0},
    }
