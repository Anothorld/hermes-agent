"""Start campaigns (proxy to the Hermes Gateway ``/v1/runs`` API).

Phase B wired the launch path through the gateway directly so the bridge
stays purely a deterministic CAL writer/reader.  See
:meth:`gateway_client.GatewayClient.start_run` for the underlying HTTP
contract.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..run_registry import (
    list_runs_for_campaign,
    merge_legacy_run_id,
    register_run,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_KOL_ORCHESTRATOR_SESSIONS = Path.home() / ".hermes/profiles/kol-orchestrator/sessions"
_MAX_TRANSCRIPT_CHARS = 4000

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
    "- Repo root for file tools is /home/pc/agent_prj/hermes-agent.\n"
    "- For search_files/read_file/write_file/patch, use repo-relative\n"
    "  paths like `plugins/kol-ops-bridge` or absolute paths under\n"
    "  `/home/pc/agent_prj/hermes-agent/`.\n"
    "- Do NOT prefix file-tool paths with `./agent_prj/hermes-agent/`.\n"
    "- For terminal/Python execution, use absolute script paths.\n"
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
    "4. Persist candidates IMMEDIATELY as you qualify them. Do not keep a\n"
    "   private in-memory candidate list and do not wait until the end of\n"
    "   discovery to write CAL. For every qualified profile, perform this\n"
    "   deterministic sequence before browsing for the next profile:\n"
    "   a) `upsert-identity --env <env> --json @/tmp/identity.json`;\n"
    "   b) `write-facts` or `write-facts-multi` for followers, region,\n"
    "      email/contact, creator type, evidence URL, and fit notes;\n"
    "   c) `add-candidate --env <env> --campaign-id <id> --json\n"
    "      @/tmp/candidate.json`;\n"
    "   d) `list-candidates --env <env> --campaign-id <id>` and verify\n"
    "      the handle is now present.\n"
    "   Every final answer MUST report the persisted count from\n"
    "   `list-candidates`, not a browser-only list.\n"
    "   After the target pool is persisted, call `resolve-relationships`.\n"
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

_APPROVAL_INSTRUCTIONS = (
    "You are continuing a KOL outreach campaign after the web console\n"
    "operator approved a shortlist.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    "- kol-ops-bridge base URL: http://127.0.0.1:8080/api/plugins/kol-ops-bridge\n"
    "  (override with HERMES_KOL_OPS_BRIDGE_BASE if needed)\n"
    "- Bridge auth header: X-Bridge-Key: $HERMES_KOL_OPS_BRIDGE_KEY\n"
    "  (already in your environment; never echo the value)\n"
    "- Repo root for file tools is /home/pc/agent_prj/hermes-agent.\n"
    "- For search_files/read_file/write_file/patch, use repo-relative\n"
    "  paths like `plugins/kol-ops-bridge` or absolute paths under\n"
    "  `/home/pc/agent_prj/hermes-agent/`.\n"
    "- Do NOT prefix file-tool paths with `./agent_prj/hermes-agent/`.\n"
    "- For terminal/Python execution, use absolute script paths.\n"
    "- ALL CAL writes/reads go through the deterministic CLI; never\n"
    "  hand-craft curl/PUT/POST. Single entry point:\n"
    "    python /home/pc/agent_prj/hermes-agent/plugins/kol-ops-bridge/\n"
    "      scripts/kol_bridge_tool.py <cmd> --env <env> [--campaign-id <id>] ...\n"
    "\n"
    "## Pipeline (run in order, do NOT skip)\n"
    "1. Treat this run as the operator gate after candidate-pool approval.\n"
    "   Do NOT run discovery again and do NOT wait for a new inbound reply.\n"
    "2. Read campaign_config, campaign candidates, and dispatch-context for\n"
    "   every approved identity ID in the input. Ignore unapproved IDs.\n"
    "3. Determine outreach path from CAL, not from prose: prefer\n"
    "   campaign_candidates.relationship_status / identity.outreach_path; if\n"
    "   absent, use relationship.total_collabs (0 => cold, >0 =>\n"
    "   reengagement). If last_outcome is disputed/content_failed/aborted,\n"
    "   open an escalation and do not draft for that KOL.\n"
    "4. For each approved cold prospect, invoke `kol-cold-outreach` with\n"
    "   identity_id, campaign_id, env. For each approved safe repeat KOL,\n"
    "   invoke `kol-reengagement-outreach`. Each child skill must return a\n"
    "   draft envelope. Do NOT write `offer.outreach_sent=true` unless an\n"
    "   email was actually sent; draft-only work writes draft-ready facts\n"
    "   and pending approval records instead.\n"
    "5. Persist every returned initial outreach draft back to CAL before\n"
    "   reporting success. Write both:\n"
    "   a) `write-event --event-type kol_initial_outreach_draft_ready` with\n"
    "      payload containing child_skill, identity_id, and draft envelope;\n"
    "   b) `write-facts-multi` with `approval.reply_draft={decision:\n"
    "      \"pending\", kind:\"initial_outreach\", child_skill, draft}`.\n"
    "   This is the durable Web approval queue.\n"
    "6. In TEST mode, any Gmail draft/test target must be test_mode_to from\n"
    "   campaign_config. Never send mail without another explicit operator\n"
    "   action. If Gmail draft creation is unavailable, the CAL\n"
    "   `approval.reply_draft` record is still required.\n"
    "7. Stop after draft records or escalations are persisted and summarize\n"
    "   draft/escalation status per approved KOL.\n"
    "\n"
    "## Failure handling\n"
    "- If bridge auth fails, stop and report the missing\n"
    "  HERMES_KOL_OPS_BRIDGE_KEY; do not bypass CAL.\n"
    "- If required contact or product facts are missing, open an escalation\n"
    "  through the bridge CLI instead of inventing values.\n"
    "- If a child skill returns no draft envelope and did not open an\n"
    "  escalation, treat that as a failed invariant: open an escalation\n"
    "  with reason `initial_outreach_draft_missing`.\n"
)


def _selected_variants(product: sqlite3.Row, variant_ids: list[str] | None) -> list[dict[str, Any]]:
    """Filter the product's known variants down to the ones the campaign opted in to.

    Empty ``variant_ids`` (or missing variants column) means "all known
    variants are in scope". Returns ``[]`` when the product has no variants
    on record — downstream callers treat that as a single implicit variant.
    """
    try:
        all_variants = json.loads(product["variants_json"] or "[]") if "variants_json" in product.keys() else []
    except (json.JSONDecodeError, TypeError):
        all_variants = []
    if not all_variants:
        return []
    if not variant_ids:
        return list(all_variants)
    wanted = {str(v) for v in variant_ids}
    return [v for v in all_variants if str(v.get("id")) in wanted]


def _compose_brief(campaign_id: str, product: sqlite3.Row, body: "StartCampaignBody") -> str:
    tags = json.loads(product["tags_json"] or "[]")
    sku_ref = product["url"] or product["sku"]
    discovery_target = body.discovery_target_count or max(
        body.headcount_target * 3, body.headcount_target + 5
    )
    selected_variants = _selected_variants(product, body.product_variant_ids)
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

    if body.deliverable_platforms:
        lines.append(
            "deliverable_platforms: " + ", ".join(body.deliverable_platforms)
        )
    if body.deliverable_count_per_platform is not None:
        lines.append(
            f"deliverable_count_per_platform: {body.deliverable_count_per_platform}"
        )
    if body.audit_standards_md and body.audit_standards_md.strip():
        lines.extend(["", "# audit_standards_md", body.audit_standards_md.strip()])

    if selected_variants:
        lines.extend(["", "# product_variants (operator-selected, KOL may pick one)"])
        for v in selected_variants:
            attrs = v.get("attributes") or {}
            attr_bits = " ".join(f"{k}={val}" for k, val in attrs.items())
            line = f"- id: {v.get('id')} | label: {v.get('label') or v.get('id')}"
            if attr_bits:
                line += f" | {attr_bits}"
            if v.get("url"):
                line += f" | url: {v.get('url')}"
            lines.append(line)

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


class ParseCampaignBody(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.post("/parse")
async def parse_campaign_intent(
    body: ParseCampaignBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
) -> dict[str, Any]:
    """Free-text → campaign_config draft (no DB write).

    Thin proxy to the bridge's deterministic regex parser. Used by the
    Campaign Wizard to preview a config before the operator confirms.
    """
    try:
        return await bridge.parse_campaign_intent(body.text, env=body.env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class FactsFromTextBody(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.post("/{campaign_id}/facts-from-text")
async def append_facts_from_text(
    campaign_id: str,
    body: FactsFromTextBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict[str, Any]:
    """Append a free-text note to ``campaign_config.extra_notes``."""
    try:
        out = await bridge.append_campaign_facts_from_text(
            campaign_id, body.text,
            appended_by=f"web:{user['email']}", env=body.env,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="campaign.facts_from_text",
        target=campaign_id,
        payload={"chars": len(body.text), "env": body.env},
    )
    return out


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
    product_variant_ids: list[str] | None = Field(
        default=None,
        description=(
            "IDs of the product variants this campaign is offering. Must be a "
            "subset of the SKU's known variants. None or empty = all known "
            "variants are in play; the KOL may pick any."
        ),
    )
    # The contract-coordinator skill blocks rendering until campaign_config
    # has these set. We capture them at launch time so the readiness gate is
    # achievable without a separate config-edit step.
    deliverable_platforms: list[str] | None = Field(
        default=None,
        description="e.g. ['instagram','tiktok','youtube']. Required for contract readiness.",
    )
    deliverable_count_per_platform: int | None = Field(
        default=None, ge=1, le=20,
        description="How many pieces of content per platform.",
    )
    audit_standards_md: str | None = Field(
        default=None, max_length=8_000,
        description="Brand/legal compliance standards the content review skill enforces.",
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
        "SELECT sku, name, url, tags_json, notes, pitch_md, selling_points, variants_json "
        "FROM products WHERE sku=?",
        (body.product_sku,),
    ).fetchone()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sku not found")
    # Validate operator-selected variants against the product catalog.
    if body.product_variant_ids:
        known = {
            str(v.get("id")) for v in (
                json.loads(product["variants_json"] or "[]")
                if product["variants_json"] else []
            )
        }
        unknown = [vid for vid in body.product_variant_ids if str(vid) not in known]
        if unknown:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown product variants: {unknown}; refresh the product detail page",
            )

    # Anti-duplicate guard. The bridge does not currently dedupe, so the
    # console owns this check. ``force=true`` lets the operator re-fire
    # intentionally (e.g. after a 402 failure) without dropping the audit row.
    if not force:
        existing = conn.execute(
            "SELECT run_id, status FROM product_campaigns WHERE campaign_id=? AND env=?",
            (campaign_id, body.env),
        ).fetchone()
        if existing is not None and existing["status"] not in {"closed", "cancelled"}:
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
    selected_variants = _selected_variants(product, body.product_variant_ids)
    color_variant_policy: str | None = None
    if selected_variants:
        labels = [v.get("label") or v.get("id") for v in selected_variants]
        color_variant_policy = "operator_selected: " + " | ".join(str(x) for x in labels)
    extra_notes_chunks: list[str] = []
    if product["selling_points"]:
        extra_notes_chunks.append(f"# selling_points\n{product['selling_points']}")
    if selected_variants:
        extra_notes_chunks.append(
            "# product_variants\n"
            + json.dumps(selected_variants, ensure_ascii=False)
        )
    extra_notes = "\n\n".join(extra_notes_chunks) or None
    try:
        upsert_body: dict[str, Any] = {
            "label": product["name"],
            "sku_whitelist": [sku_ref],
            "paid_ceiling": body.budget_per_kol,
            "contract_required": True,
            "test_mode_to": body.test_mode_to,
            "env": body.env,
        }
        if color_variant_policy:
            upsert_body["color_variant_policy"] = color_variant_policy
        if extra_notes:
            upsert_body["extra_notes"] = extra_notes
        if body.deliverable_platforms:
            upsert_body["deliverable_platforms"] = body.deliverable_platforms
        if body.deliverable_count_per_platform is not None:
            upsert_body["deliverable_count_per_platform"] = body.deliverable_count_per_platform
        if body.audit_standards_md and body.audit_standards_md.strip():
            upsert_body["audit_standards_md"] = body.audit_standards_md.strip()
        await bridge.upsert_campaign(campaign_id, upsert_body)
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
                         (sku, campaign_id, env, run_id, test_mode_to, started_at, started_by_user_id, status)
                     VALUES (?,?,?,?,?,?,?, 'running')
           ON CONFLICT(campaign_id, env) DO UPDATE SET
             run_id=excluded.run_id,
                         test_mode_to=excluded.test_mode_to,
             started_at=excluded.started_at,
             started_by_user_id=excluded.started_by_user_id,
             status='running'""",
                (product["sku"], campaign_id, body.env, run_id, body.test_mode_to, now, user["id"]),
    )
    if isinstance(run_id, str) and run_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=body.env,
            run_id=run_id,
            kind="outreach",
            session_id=f"kol-campaign:{body.env}:{campaign_id}",
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
    test_mode_to: str | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


def _clip_text(value: Any, limit: int = _MAX_TRANSCRIPT_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]"


def _session_file(campaign_id: str, env: str) -> Path:
    return _KOL_ORCHESTRATOR_SESSIONS / f"session_kol-campaign:{env}:{campaign_id}.json"


def _tool_call_label(call: dict[str, Any]) -> str:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = fn.get("name") or call.get("name") or "tool"
    args = fn.get("arguments") or call.get("arguments") or ""
    return f"{name}({ _clip_text(args, 1200) })"


def _transcript_items(campaign_id: str, env: str, limit: int) -> list[dict[str, Any]] | None:
    path = _session_file(campaign_id, env)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not read session transcript: {exc}") from exc
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []
    items: list[dict[str, Any]] = []
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            items.append({
                "index": index,
                "kind": role,
                "label": "operator" if role == "user" else "assistant",
                "message": _clip_text(content),
            })
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, dict):
                    items.append({
                        "index": index,
                        "kind": "tool_call",
                        "label": "tool call",
                        "message": _tool_call_label(call),
                    })
        if role == "tool":
            name = str(msg.get("name") or "tool")
            body = content if isinstance(content, str) else ""
            kind = "error" if "\"success\": false" in body or "\"error\"" in body.lower() else "tool_result"
            items.append({
                "index": index,
                "kind": kind,
                "label": name,
                "message": _clip_text(body),
            })
    return items[-limit:]


def _coerce_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _latest_campaign_run_id(
    conn: sqlite3.Connection, campaign_id: str, env: str
) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM product_campaigns WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    if row is None:
        return None
    run_id = row["run_id"]
    return run_id if isinstance(run_id, str) and run_id else None


async def _escalation_snapshot(
    bridge: BridgeClient, escalation_id: int | None, env: str
) -> dict[str, Any] | None:
    if escalation_id is None:
        return None
    try:
        rows = await bridge.list_escalations(env=env)
    except BridgeError:
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == escalation_id:
            return row
    return None


def _pick_first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_resume_operator_message(
    *,
    campaign_id: str,
    env: str,
    output: dict[str, Any],
    escalation: dict[str, Any] | None,
) -> str:
    parts = [
        "# escalation_resume",
        f"campaign_id: {campaign_id}",
        f"env: {env}",
    ]
    escalation_id = output.get("escalation_id")
    identity_id = output.get("identity_id")
    if escalation_id is not None:
        parts.append(f"escalation_id: {escalation_id}")
    if identity_id is not None:
        parts.append(f"identity_id: {identity_id}")
    if escalation:
        reason = _pick_first_text(escalation, "reason", "rule_id", "title")
        question = _pick_first_text(escalation, "question", "prompt", "message")
        answer = _pick_first_text(escalation, "operator_answer", "answer", "resolution_note")
        context = _pick_first_text(escalation, "resume_context", "context")
        if reason:
            parts.append(f"reason: {reason}")
        if question:
            parts.append(f"question: {question}")
        if answer:
            parts.append(f"operator_answer: {answer}")
        if context:
            parts.append(f"resume_context: {context}")
    return "\n".join(parts)


def _format_resume_assistant_message(output: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in (
        "decision",
        "facts_written",
        "resume_action",
        "next_required_fact",
        "public_creator_email_added",
        "override_config_patch",
        "child_escalation_id",
        "force_human_takeover_hint",
        "next_action",
    ):
        if key in output and output[key] is not None:
            lines.append(f"{key}: {_clip_text(output[key])}")
    return "\n".join(lines) if lines else _clip_text(output)


async def _resume_transcript_from_events(
    *,
    campaign_id: str,
    env: str,
    bridge: BridgeClient,
) -> list[dict[str, Any]] | None:
    try:
        events = await bridge.recent_events(env=env, limit=80, campaign_id=campaign_id)
    except BridgeError:
        return None
    processed: dict[str, Any] | None = None
    resumed: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict) or event.get("campaign_id") != campaign_id:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = event.get("event_type")
        if event_type == "escalation_resume_processed" and processed is None:
            processed = event
            escalation_id = payload.get("escalation_id")
            for candidate in events:
                if not isinstance(candidate, dict):
                    continue
                candidate_payload = (
                    candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
                )
                if (
                    isinstance(candidate, dict)
                    and candidate.get("event_type") == "escalation_resumed"
                    and candidate_payload.get("escalation_id") == escalation_id
                ):
                    resumed = candidate
                    break
            break
        if event_type == "escalation_resumed" and resumed is None:
            resumed = event
    source_event = processed or resumed
    if source_event is None:
        return None
    source_payload = (
        source_event.get("payload") if isinstance(source_event.get("payload"), dict) else {}
    )
    resumed_payload = resumed.get("payload") if isinstance(resumed, dict) and isinstance(resumed.get("payload"), dict) else {}
    output = {
        "skill": "kol-escalation-resumer",
        "escalation_id": source_payload.get("escalation_id") or resumed_payload.get("escalation_id"),
        "identity_id": source_event.get("identity_id"),
        "decision": source_payload.get("decision"),
        "resume_action": source_payload.get("resume_action") or resumed_payload.get("resume_action"),
        "next_required_fact": source_payload.get("next_required_fact"),
        "public_creator_email_added": source_payload.get("public_creator_email_added"),
    }
    escalation = {
        "reason": resumed_payload.get("reason") or source_payload.get("reason"),
        "operator_answer": resumed_payload.get("operator_answer") or source_payload.get("operator_answer"),
    }
    return [
        {
            "index": 0,
            "ts": resumed.get("ts") if isinstance(resumed, dict) else source_event.get("ts"),
            "kind": "user",
            "label": "operator",
            "message": _format_resume_operator_message(
                campaign_id=campaign_id,
                env=env,
                output=output,
                escalation=escalation,
            ),
        },
        {
            "index": 1,
            "ts": source_event.get("ts"),
            "kind": "assistant",
            "label": "assistant",
            "message": _format_resume_assistant_message(output),
        },
    ]


async def _resume_transcript_items(
    *,
    campaign_id: str,
    env: str,
    conn: sqlite3.Connection,
    gateway: GatewayClient,
    bridge: BridgeClient,
) -> list[dict[str, Any]] | None:
    run_id = _latest_campaign_run_id(conn, campaign_id, env)
    if run_id is None:
        return await _resume_transcript_from_events(
            campaign_id=campaign_id, env=env, bridge=bridge
        )
    try:
        run = await gateway.get_run(run_id)
    except GatewayError as exc:
        run = None
    if not isinstance(run, dict):
        return await _resume_transcript_from_events(
            campaign_id=campaign_id, env=env, bridge=bridge
        )
    output = _coerce_json_object(run.get("output"))
    if output is None or output.get("skill") != "kol-escalation-resumer":
        return await _resume_transcript_from_events(
            campaign_id=campaign_id, env=env, bridge=bridge
        )
    escalation_id = output.get("escalation_id")
    escalation = await _escalation_snapshot(
        bridge, escalation_id if isinstance(escalation_id, int) else None, env
    )
    items = [
        {
            "index": 0,
            "kind": "user",
            "label": "operator",
            "message": _format_resume_operator_message(
                campaign_id=campaign_id,
                env=env,
                output=output,
                escalation=escalation,
            ),
        },
        {
            "index": 1,
            "kind": "assistant",
            "label": "assistant",
            "message": _format_resume_assistant_message(output),
        },
    ]
    return items


@router.get("/{campaign_id}/agent-log")
async def agent_log(
    campaign_id: str,
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
    limit: int = Query(120, ge=1, le=500),
) -> dict:
    """Return recent visible transcript items for one campaign session.

    Hidden model reasoning fields are intentionally omitted; this endpoint only
    exposes user-visible assistant text plus tool call/result records.
    """
    resume_items = await _resume_transcript_items(
        campaign_id=campaign_id,
        env=env,
        conn=conn,
        gateway=gateway,
        bridge=bridge,
    )
    if resume_items is not None:
        return {
            "campaign_id": campaign_id,
            "env": env,
            "source": "resume-session",
            "items": resume_items[-limit:],
        }
    transcript = _transcript_items(campaign_id, env, limit)
    if transcript is not None:
        return {
            "campaign_id": campaign_id,
            "env": env,
            "source": "session",
            "items": transcript[-limit:],
        }
    return {"campaign_id": campaign_id, "env": env, "source": "session", "items": []}


# --------------------------------------------------------------- agent-stream
# Multi-run SSE aggregator. The campaign-level transcript panel needs to see
# *all* gateway runs spawned for a campaign — outreach, reply-dispatcher,
# preview-draft, escalation-resume — not just product_campaigns.run_id. The
# product_campaign_runs registry holds the active list; for each registered
# run_id we open a parallel SSE subscription to the gateway and merge frames
# into one ordered output stream.

def _sse_frame(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def _proxy_run_events(
    *,
    run_id: str,
    kind: str,
    out_queue: asyncio.Queue,
    settings,
    stop_event: asyncio.Event,
) -> None:
    """Subscribe to one gateway run's SSE feed and push frames into out_queue.

    Each gateway frame is rewrapped with ``run_id`` + ``kind`` so the
    frontend can label which run a line came from. Quietly terminates on
    gateway 404 (run already evicted) or connection failure — the
    aggregator continues serving the remaining runs.
    """
    url = f"{settings.gateway_base.rstrip('/')}/v1/runs/{run_id}/events"
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if settings.gateway_key:
        headers["Authorization"] = f"Bearer {settings.gateway_key}"
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 404:
                    await out_queue.put(_sse_frame(
                        "run.evicted", {"run_id": run_id, "kind": kind}
                    ))
                    return
                if resp.status_code >= 400:
                    await out_queue.put(_sse_frame(
                        "run.error",
                        {"run_id": run_id, "kind": kind,
                         "status": resp.status_code},
                    ))
                    return
                # Parse the gateway SSE stream line-by-line so we can
                # re-wrap each data frame with run_id metadata. We do
                # NOT pass keepalive comments through; this endpoint
                # emits its own keepalives at the aggregator level.
                event_name = "message"
                data_lines: list[str] = []
                async for raw_line in resp.aiter_lines():
                    if stop_event.is_set():
                        return
                    if raw_line == "":
                        if data_lines:
                            data_str = "\n".join(data_lines)
                            try:
                                payload_obj: Any = json.loads(data_str)
                            except json.JSONDecodeError:
                                payload_obj = {"raw": data_str}
                            await out_queue.put(_sse_frame(
                                event_name if event_name != "message" else "run.event",
                                {"run_id": run_id, "kind": kind,
                                 "event": event_name,
                                 "payload": payload_obj},
                            ))
                        event_name = "message"
                        data_lines = []
                        continue
                    if raw_line.startswith(":"):
                        continue
                    if raw_line.startswith("event:"):
                        event_name = raw_line[6:].strip() or "message"
                    elif raw_line.startswith("data:"):
                        data_lines.append(raw_line[5:].lstrip())
    except (httpx.HTTPError, asyncio.CancelledError):
        pass
    finally:
        await out_queue.put(_sse_frame(
            "run.closed", {"run_id": run_id, "kind": kind}
        ))


@router.get("/{campaign_id}/agent-stream")
async def agent_stream(
    campaign_id: str,
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
    limit: int = Query(120, ge=1, le=500),
) -> StreamingResponse:
    """Live transcript feed for a campaign.

    First emits a ``snapshot`` event with the latest finalised transcript
    items + the registry of runs we're about to subscribe to. Then opens
    parallel SSE subscriptions to every registered gateway run for the
    campaign and forwards rewrapped frames as they arrive.
    """
    # Backfill legacy run_id from product_campaigns into the registry so
    # campaigns that pre-date the registry table still stream their main
    # outreach run.
    legacy_run_id = _latest_campaign_run_id(conn, campaign_id, env)
    merge_legacy_run_id(
        conn,
        campaign_id=campaign_id,
        env=env,
        legacy_run_id=legacy_run_id,
        legacy_kind="outreach",
    )
    initial_runs = list_runs_for_campaign(
        conn, campaign_id=campaign_id, env=env, limit=20
    )
    settings = get_settings()

    snapshot_items: list[dict[str, Any]] = []
    resume_items = await _resume_transcript_items(
        campaign_id=campaign_id, env=env, conn=conn,
        gateway=gateway, bridge=bridge,
    )
    if resume_items is not None:
        snapshot_items = resume_items[-limit:]
    else:
        transcript = _transcript_items(campaign_id, env, limit)
        if transcript is not None:
            snapshot_items = transcript[-limit:]

    async def producer() -> AsyncIterator[bytes]:
        out_queue: asyncio.Queue[bytes] = asyncio.Queue()
        stop_event = asyncio.Event()
        yield _sse_frame("snapshot", {
            "campaign_id": campaign_id,
            "env": env,
            "items": snapshot_items,
            "runs": initial_runs,
        })
        # Spawn one proxy per known run; track names so we can adopt new
        # runs discovered later (reply-dispatcher firing mid-stream).
        tasks: dict[str, asyncio.Task] = {}
        for r in initial_runs:
            tasks[r["run_id"]] = asyncio.create_task(_proxy_run_events(
                run_id=r["run_id"], kind=r["kind"],
                out_queue=out_queue, settings=settings, stop_event=stop_event,
            ))
        try:
            keepalive_at = asyncio.get_event_loop().time() + 25.0
            poll_at = asyncio.get_event_loop().time() + 5.0
            while True:
                timeout = max(
                    0.5,
                    min(
                        keepalive_at - asyncio.get_event_loop().time(),
                        poll_at - asyncio.get_event_loop().time(),
                    ),
                )
                try:
                    frame = await asyncio.wait_for(out_queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    frame = None
                now = asyncio.get_event_loop().time()
                if frame is not None:
                    yield frame
                if now >= keepalive_at:
                    yield b": keepalive\n\n"
                    keepalive_at = now + 25.0
                if now >= poll_at:
                    # New runs registered since stream start (e.g.,
                    # reply-dispatcher tick fired) — adopt them.
                    fresh = list_runs_for_campaign(
                        conn, campaign_id=campaign_id, env=env, limit=20
                    )
                    for r in fresh:
                        if r["run_id"] not in tasks:
                            tasks[r["run_id"]] = asyncio.create_task(_proxy_run_events(
                                run_id=r["run_id"], kind=r["kind"],
                                out_queue=out_queue, settings=settings,
                                stop_event=stop_event,
                            ))
                            yield _sse_frame("run.added", {
                                "run_id": r["run_id"], "kind": r["kind"],
                                "started_at": r["started_at"],
                            })
                    poll_at = now + 5.0
        except asyncio.CancelledError:
            raise
        finally:
            stop_event.set()
            for t in tasks.values():
                t.cancel()
            for t in tasks.values():
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        producer(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        out = await bridge.get_shortlist(campaign_id, env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    raw_candidates = out.get("candidates", []) if isinstance(out, dict) else []
    candidates: list[dict[str, Any]] = []
    for row in raw_candidates:
        if not isinstance(row, dict):
            continue
        if row.get("candidate_status") in {"rejected", "archived"}:
            continue
        identity_id = row.get("identity_id")
        ident: dict[str, Any] = {}
        if isinstance(identity_id, int):
            try:
                ident = await bridge.get_identity(identity_id)
            except BridgeError:
                ident = {}
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        score = row.get("discovery_score")
        score_pct = round(score * 100) if isinstance(score, (int, float)) else None
        handle = (
            ident.get("primary_handle")
            or row.get("primary_handle")
            or payload.get("handle")
            or (f"id{identity_id}" if identity_id is not None else "unknown")
        )
        candidates.append({
            "handle": str(handle).lstrip("@"),
            "platform": ident.get("platform") or row.get("platform"),
            "identity_id": identity_id if isinstance(identity_id, int) else None,
            "display_name": ident.get("display_name"),
            "audience_fit": payload.get("audience_fit") or payload.get("final_fit") or score_pct,
            "brand_safety": payload.get("brand_safety"),
            "engagement_quality": payload.get("engagement_quality") or payload.get("showcase_score"),
            "niche_match": payload.get("niche_match") or payload.get("match_score"),
            "reason": row.get("review_reason") or payload.get("reason") or row.get("source"),
        })
    return {"campaign_id": campaign_id, "candidates": candidates}


def _compose_approval_brief(
    *,
    campaign_id: str,
    env: str,
    selected_rows: list[dict[str, Any]],
    actor_email: str,
    test_mode_to: str | None,
) -> str:
    lines = [
        "# campaign_approval",
        f"campaign_id: {campaign_id}",
        f"mode: {env}",
        f"approved_by: {actor_email}",
        f"test_mode_to: {test_mode_to or ''}",
        "",
        "# selected_kols",
    ]
    for row in selected_rows:
        lines.append(
            f"- identity_id: {row['identity_id']}\n"
            f"  handle: {row['handle']}"
        )
    lines.extend([
        "",
        "# required_next_step",
        "Continue the same KOL campaign after operator approval.",
        "Do NOT run discovery again. Read the selected candidates from CAL using",
        "the deterministic CLI and prepare outreach drafts only for the approved",
        "identity IDs above.",
        "",
        "## Runtime contract",
        "- Use /home/pc/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py.",
        "- Every CLI call MUST pass --env matching `mode` above.",
        "- If bridge auth fails, stop and report the missing HERMES_KOL_OPS_BRIDGE_KEY; do not bypass CAL.",
        "- In TEST mode, route any draft/test outbound email to test_mode_to above.",
        "- Create Gmail drafts or deterministic draft records only; never send mail without another explicit operator action.",
        "- Record progress/events through the bridge CLI so the console can show what happened.",
    ])
    return "\n".join(lines)


def _recover_test_mode_to(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    current: str | None,
    override: str | None = None,
) -> str | None:
    current_value = (current or "").strip()
    if current_value:
        return current_value
    override_value = (override or "").strip()
    if override_value:
        conn.execute(
            "UPDATE product_campaigns SET test_mode_to=? "
            "WHERE campaign_id=? AND env=? AND (test_mode_to IS NULL OR test_mode_to='')",
            (override_value, campaign_id, env),
        )
        return override_value
    row = conn.execute(
        "SELECT payload_json FROM audit_log "
        "WHERE action='campaign.start' AND target=? "
        "ORDER BY ts DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return None
    recovered = str(payload.get("test_mode_to") or "").strip()
    if recovered:
        conn.execute(
            "UPDATE product_campaigns SET test_mode_to=? "
            "WHERE campaign_id=? AND env=? AND (test_mode_to IS NULL OR test_mode_to='')",
            (recovered, campaign_id, env),
        )
    return recovered or None


@router.post("/{campaign_id}/approve-shortlist")
async def approve_shortlist(
    campaign_id: str,
    body: ApproveShortlistBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Approve candidates in CAL, then launch the post-approval agent run."""
    selected = {h.strip().lstrip("@").lower() for h in body.selected_handles if h.strip()}
    identity_ids: list[int] = []
    selected_rows: list[dict[str, Any]] = []
    try:
        candidates = await bridge.list_candidates(campaign_id, env=body.env)
        for row in candidates:
            identity_id = row.get("identity_id")
            if not isinstance(identity_id, int):
                continue
            try:
                ident = await bridge.get_identity(identity_id)
            except BridgeError:
                ident = {}
            handle = str(ident.get("primary_handle") or row.get("primary_handle") or "").lstrip("@").lower()
            if handle in selected:
                identity_ids.append(identity_id)
                selected_rows.append({"identity_id": identity_id, "handle": handle})
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if selected and not identity_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "selected handles did not match any candidates")
    campaign_row = conn.execute(
        "SELECT sku, test_mode_to FROM product_campaigns WHERE campaign_id=? AND env=?",
        (campaign_id, body.env),
    ).fetchone()
    test_mode_to = _recover_test_mode_to(
        conn,
        campaign_id=campaign_id,
        env=body.env,
        current=campaign_row["test_mode_to"] if campaign_row else None,
        override=body.test_mode_to,
    )
    if body.env == "TEST" and not test_mode_to:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "test_mode_to missing for TEST campaign; restart the campaign or pass test_mode_to",
        )
    campaign_update: dict[str, Any] = {"env": body.env}
    if campaign_row and campaign_row["sku"]:
        campaign_update["sku_whitelist"] = [campaign_row["sku"]]
    if test_mode_to:
        campaign_update["test_mode_to"] = test_mode_to
    try:
        await bridge.upsert_campaign(campaign_id, campaign_update)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    payload = {
        "identity_ids": identity_ids,
        "selected_by": f"web:{user['email']}",
        "env": body.env,
    }
    try:
        out = await bridge.approve_shortlist(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    event_ids: list[int] = []
    for row in selected_rows:
        event = await bridge.write_event({
            "identity_id": row["identity_id"],
            "campaign_id": campaign_id,
            "event_type": "approved",
            "goal": "outreach",
            "lane": "commerce",
            "actor": f"web:{user['email']}",
            "payload": {
                "product_sku": campaign_row["sku"] if campaign_row else None,
                "selected_handles": body.selected_handles,
                "selected_identity_ids": identity_ids,
            },
            "env": body.env,
        })
        event_id = event.get("event_id")
        if isinstance(event_id, int):
            event_ids.append(event_id)
    approval_brief = _compose_approval_brief(
        campaign_id=campaign_id,
        env=body.env,
        selected_rows=selected_rows,
        actor_email=user["email"],
        test_mode_to=test_mode_to,
    )
    try:
        run = await gateway.start_run(
            input=approval_brief,
            instructions=_APPROVAL_INSTRUCTIONS,
            session_id=f"kol-campaign:{body.env}:{campaign_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    new_run_id = run.get("run_id")
    conn.execute(
        "UPDATE product_campaigns SET run_id=?, status='running' "
        "WHERE campaign_id=? AND env=?",
        (new_run_id, campaign_id, body.env),
    )
    if isinstance(new_run_id, str) and new_run_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=body.env,
            run_id=new_run_id,
            kind="outreach",
            session_id=f"kol-campaign:{body.env}:{campaign_id}",
        )
    write_audit(conn, actor_user_id=user["id"], action="campaign.approve_shortlist",
                target=campaign_id, payload={**payload, "selected_handles": body.selected_handles, "run_id": new_run_id, "event_ids": event_ids})
    return {**out, "run_id": new_run_id, "approved_count": len(identity_ids), "event_ids": event_ids}


# Goal status values the bridge writes (cal.update_goal_state_for):
# inactive / active / satisfied / blocked / skipped / aborted. Only `active`
# and `blocked` count as in-progress for kanban bucketing. Other states
# (especially `inactive`) leave the lane empty so a downstream goal is NOT
# rendered before its turn.
_ACTIVE_GOAL_STATES = {"active", "blocked"}


def _pick_active_per_lane(lanes: dict) -> dict:
    """Bridge returns ``{lane: [goal_state,...]}``; the console renders one
    ``goal`` chip per lane.

    Rules:
    - Prefer the first goal whose status is in ``_ACTIVE_GOAL_STATES``.
    - Otherwise prefer the LAST ``satisfied`` goal (so a fully-completed
      lane still shows "what we last finished" rather than a downstream
      ``inactive`` goal that hasn't started).
    - If neither exists, return ``None`` for the lane so the FE leaves
      that column empty for this KOL.
    """
    out: dict = {"commerce": None, "fulfillment": None, "publish": None, "meta": None}
    for lane, states in (lanes or {}).items():
        if not states:
            continue
        active = next((s for s in states if s.get("status") in _ACTIVE_GOAL_STATES), None)
        if active is None:
            satisfied = [s for s in states if s.get("status") == "satisfied"]
            if not satisfied:
                continue
            chosen = satisfied[-1]
        else:
            chosen = active
        out[lane] = {
            "goal": chosen.get("goal"),
            "state": chosen.get("status") or "inactive",
            "missing_facts": chosen.get("missing_facts") or [],
            "blocked_reason": chosen.get("blocking_escalation_id") or None,
        }
    return out


class PatchCampaignConfigBody(BaseModel):
    """Subset of CampaignConfigUpsertBody the operator may tweak after a
    launch. Used to clear readiness blockers (deliverables / audit standards
    / variant policy) without re-running the gateway agent."""

    deliverable_platforms: list[str] | None = None
    deliverable_count_per_platform: int | None = Field(default=None, ge=1, le=20)
    audit_standards_md: str | None = Field(default=None, max_length=8_000)
    color_variant_policy: str | None = Field(default=None, max_length=2_000)
    extra_notes: str | None = Field(default=None, max_length=8_000)
    paid_ceiling: float | None = Field(default=None, gt=0)
    contract_required: bool | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


@router.patch("/{campaign_id}/config")
async def patch_campaign_config(
    campaign_id: str,
    body: PatchCampaignConfigBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """Persist operator-supplied campaign_config edits.

    The bridge ``PUT /campaigns/{id}`` is itself an upsert — we send only the
    fields the operator actually changed (model_dump(exclude_none=True))
    so untouched columns retain their value.
    """
    payload = body.model_dump(exclude_none=True)
    env = payload.pop("env", "TEST")
    payload["env"] = env
    if "audit_standards_md" in payload:
        payload["audit_standards_md"] = payload["audit_standards_md"].strip() or None
        if payload["audit_standards_md"] is None:
            payload.pop("audit_standards_md")
    try:
        await bridge.upsert_campaign(campaign_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="campaign.config_patch",
        target=campaign_id, payload=payload,
    )
    return {"ok": True, "campaign_id": campaign_id, "patched": list(payload.keys())}


# ---------------------------------------------------------------------------
# Contract readiness
# ---------------------------------------------------------------------------
#
# Pre-flight checklist for the contract phase. Aggregates the per-KOL
# state the contract-coordinator skill validates at render time (Step I.1
# in kol-contract-coordinator/SKILL.md) and surfaces it BEFORE the agent
# attempts to send / sign anything. The console renders a green/red
# checklist so the operator can fix gaps proactively instead of waiting
# for an escalation to fire.
#
# Required (matches contract-coordinator + render_contract.py):
#   identity.full_name, primary_email, phone
#   fulfillment.shipping_address  → street, city, state, zip, email, phone
#                                    AND a confirmed full_name
#   product.specs                  → derived from product variants + lock
#   product.link                   → product url or selected variant url
#   campaign.deliverables          → at least 1 deliverable_platform with
#                                    a positive count_per_platform
#   offer.fee | compensation_mode  → fee for "cash", or mode=="free_product"
#                                    is acceptable
_REQUIRED_ADDRESS_FIELDS = ("street", "city", "state", "zip", "email", "phone", "full_name")


def _addr_value(addr: Any, *keys: str) -> str | None:
    if not isinstance(addr, dict):
        return None
    for key in keys:
        value = addr.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # ``state`` is sometimes encoded as ``region``; ``zip`` as
        # ``postal_code``. Normalise here so the readiness view doesn't
        # complain about a missing zip when the underlying shipping skill
        # used postal_code.
        if key == "state":
            r = addr.get("region")
            if isinstance(r, str) and r.strip():
                return r.strip()
        if key == "zip":
            for alt in ("postal_code", "postcode", "zip_code"):
                v = addr.get(alt)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def _check(ok: bool, value: Any, *, label: str, why: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "value": value, "label": label, "why": why}


@router.get("/{campaign_id}/contract-readiness")
async def contract_readiness(
    campaign_id: str,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    identity_id: int = Query(..., ge=1),
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
) -> dict[str, Any]:
    """Return the contract-readiness checklist for one (campaign, KOL).

    Schema::

        {
          "campaign_id": "...", "identity_id": 42, "env": "TEST",
          "ready": false, "blockers": ["identity.full_name", ...],
          "sections": {
            "identity": {"full_name": {ok, value, label, why}, ...},
            "shipping_address": {"street": {...}, "city": {...}, ...},
            "product":  {"specs": {...}, "link": {...}, "variant_locked": {...}},
            "campaign": {"deliverables": {...}, "contract_required": {...}},
            "offer":    {"compensation_mode": {...}, "fee": {...}},
          }
        }
    """
    # 1. KOL identity row.
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError:
        ident = {}
    if not isinstance(ident, dict):
        ident = {}

    # 2. Campaign config (deliverables, contract_required) — best-effort.
    try:
        camp_cfg = await bridge.get_campaign(campaign_id)
    except BridgeError:
        camp_cfg = {}
    if not isinstance(camp_cfg, dict):
        camp_cfg = {}

    # 3. Per-(identity, campaign) facts.
    try:
        facts_resp = await bridge.read_facts(identity_id, campaign_id=campaign_id, env=env)
        facts: dict[str, Any] = facts_resp.get("facts", {}) if isinstance(facts_resp, dict) else {}
    except BridgeError:
        facts = {}

    # 4. Local product row (variants + url + selling points).
    pc_row = conn.execute(
        "SELECT sku FROM product_campaigns WHERE campaign_id=? AND env=? LIMIT 1",
        (campaign_id, env),
    ).fetchone()
    product_row = None
    if pc_row is not None:
        product_row = conn.execute(
            "SELECT sku, name, url, variants_json FROM products WHERE sku=?",
            (pc_row["sku"],),
        ).fetchone()

    # --- identity facts ---
    full_name = (
        ident.get("full_name")
        or facts.get("identity.full_name")
        or ident.get("display_name")
    )
    primary_email = ident.get("primary_email") or facts.get("identity.primary_email")
    phone = (
        ident.get("phone")
        or facts.get("identity.phone")
        or facts.get("identity.phone_number")
    )

    # --- shipping address ---
    addr = facts.get("fulfillment.shipping_address") or ident.get("default_shipping_address")
    addr_check = {}
    for key in _REQUIRED_ADDRESS_FIELDS:
        if key == "full_name":
            val = _addr_value(addr, "full_name", "name") or full_name
            addr_check[key] = _check(bool(val), val, label="收件人姓名")
        elif key == "email":
            val = _addr_value(addr, "email") or primary_email
            addr_check[key] = _check(bool(val), val, label="Email")
        elif key == "phone":
            val = _addr_value(addr, "phone", "phone_number") or phone
            addr_check[key] = _check(bool(val), val, label="Phone")
        else:
            val = _addr_value(addr, key)
            addr_check[key] = _check(bool(val), val, label=key.title())

    # --- product (specs / link / variant lock) ---
    variants = []
    if product_row is not None:
        try:
            variants = json.loads(product_row["variants_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            variants = []
    sku_locked = facts.get("offer.sku_locked")
    variant_locked = facts.get("offer.color_or_variant_locked")
    variant_match = next(
        (v for v in variants if str(v.get("id")) == str(variant_locked)),
        None,
    )
    product_link = None
    if variant_match and variant_match.get("url"):
        product_link = variant_match["url"]
    elif product_row is not None:
        product_link = product_row["url"]
    product_name = product_row["name"] if product_row is not None else None
    specs_bits: list[str] = []
    if product_name:
        specs_bits.append(str(product_name))
    if variant_match:
        specs_bits.append(str(variant_match.get("label") or variant_match.get("id")))
    elif sku_locked:
        specs_bits.append(str(sku_locked))
    product_specs = " · ".join(specs_bits) if specs_bits else None

    # --- campaign deliverables ---
    deliverable_platforms = camp_cfg.get("deliverable_platforms") or []
    deliverable_count = camp_cfg.get("deliverable_count_per_platform")
    has_deliverables = bool(deliverable_platforms) and isinstance(deliverable_count, int) and deliverable_count > 0
    contract_required = bool(camp_cfg.get("contract_required", True))

    # --- offer (fee / compensation_mode) ---
    compensation_mode = facts.get("offer.compensation_mode")
    agreed_terms = facts.get("offer.agreed_terms")
    fee_value: Any = None
    if isinstance(agreed_terms, dict):
        fee_value = agreed_terms.get("fee") or agreed_terms.get("amount")
    elif isinstance(agreed_terms, (int, float)):
        fee_value = agreed_terms
    fee_ok = (
        compensation_mode in ("free_product", "gifted", "commission_no_product")
        or (fee_value is not None and (isinstance(fee_value, (int, float)) and fee_value > 0))
    )

    sections: dict[str, Any] = {
        "identity": {
            "full_name": _check(bool(full_name), full_name, label="Full Name"),
            "primary_email": _check(bool(primary_email), primary_email, label="Email"),
            "phone": _check(bool(phone), phone, label="Phone"),
        },
        "shipping_address": addr_check,
        "product": {
            "specs": _check(
                bool(product_specs) and bool(variant_match or (variants == [] and sku_locked)),
                product_specs,
                label="PRODUCT_SPECS",
                why=(
                    None if (variant_match or (variants == [] and sku_locked))
                    else "尚未确认 KOL 选定的 variant (offer.color_or_variant_locked)"
                ),
            ),
            "link": _check(bool(product_link), product_link, label="PRODUCT_LINK"),
            "variant_locked": _check(
                variants == [] or bool(variant_match),
                (variant_match or {"id": variant_locked}) if variant_locked else None,
                label="Variant locked",
                why=(
                    None if (variants == [] or variant_match)
                    else "offer.color_or_variant_locked 未在产品 variant 列表里 — 检查 KOL 选品"
                ),
            ),
        },
        "campaign": {
            "deliverables": _check(
                has_deliverables,
                {"platforms": deliverable_platforms, "count": deliverable_count},
                label="Deliverables",
                why=(
                    None if has_deliverables
                    else "campaign_config 还缺 deliverable_platforms / deliverable_count_per_platform"
                ),
            ),
            "contract_required": _check(
                True, contract_required,
                label="contract_required",
                why=None if contract_required else "此 campaign 标了 contract_required=false，可跳过合同",
            ),
        },
        "offer": {
            "compensation_mode": _check(
                bool(compensation_mode), compensation_mode, label="Compensation mode",
            ),
            "fee": _check(
                fee_ok, fee_value if fee_value is not None else agreed_terms,
                label="Fee / agreed terms",
                why=None if fee_ok else (
                    "cash 模式下必须有 offer.agreed_terms 包含 numeric fee；"
                    "free_product / commission_no_product 可跳过"
                ),
            ),
        },
    }

    # If contract is explicitly not required, the whole readiness gate is
    # auto-satisfied — surface that without iterating through every blocker.
    if not contract_required:
        return {
            "campaign_id": campaign_id,
            "identity_id": identity_id,
            "env": env,
            "ready": True,
            "blockers": [],
            "skipped_reason": "contract_required=false",
            "sections": sections,
        }

    blockers: list[str] = []
    for section_name, checks in sections.items():
        if section_name == "campaign":
            # contract_required is a status, not a gating check
            for key, chk in checks.items():
                if key == "contract_required":
                    continue
                if not chk["ok"]:
                    blockers.append(f"{section_name}.{key}")
            continue
        for key, chk in checks.items():
            if not chk["ok"]:
                blockers.append(f"{section_name}.{key}")
    return {
        "campaign_id": campaign_id,
        "identity_id": identity_id,
        "env": env,
        "ready": len(blockers) == 0,
        "blockers": blockers,
        "sections": sections,
    }


class ShippingAddressBody(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    street: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    zip: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)


class IdentityFactsPatchBody(BaseModel):
    """Operator-side writes for the contract-readiness blockers."""

    identity_id: int = Field(ge=1)
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")
    # Updates that map onto kol_identity columns (handled via bridge PUT).
    primary_handle: str | None = None
    platform: str | None = Field(default=None, max_length=80)
    primary_email: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    # Updates that live as facts in the ``identity`` namespace.
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)
    # Structured shipping address. Persisted both as fact
    # (fulfillment.shipping_address) and as identity-column
    # default_shipping_address so the next campaign reuses it.
    shipping_address: ShippingAddressBody | None = None
    campaign_id: str | None = None


@router.post("/{campaign_id}/contract-readiness/fill-blockers")
async def fill_contract_blockers(
    campaign_id: str,
    body: IdentityFactsPatchBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """One-shot write to clear the contract-readiness blockers.

    Splits the operator-supplied fields into three sinks:

    * ``kol_identity`` columns (primary_email, display_name,
      default_shipping_address) — bridge ``PUT /identities``.
    * ``identity.*`` facts (full_name, phone) — bridge ``POST /facts/{id}``.
    * ``fulfillment.shipping_address`` fact — same endpoint, different
      namespace, scoped to ``campaign_id`` so the campaign-specific
      shipping address doesn't retro-affect other campaigns.

    Returns the list of sinks actually touched so the UI can give targeted
    feedback ("identity updated · shipping address written").
    """
    # 1) Look up the existing identity so we have its handle/platform.
    try:
        ident = await bridge.get_identity(body.identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not isinstance(ident, dict) or not ident.get("primary_handle"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    touched: list[str] = []
    env = body.env
    cid = body.campaign_id or campaign_id

    addr_obj: dict[str, Any] | None = None
    if body.shipping_address is not None:
        raw = body.shipping_address.model_dump(exclude_none=True)
        # Strip whitespace; treat empty strings as absent.
        cleaned = {k: v.strip() for k, v in raw.items() if isinstance(v, str) and v.strip()}
        if cleaned:
            addr_obj = cleaned

    # 2) Update kol_identity columns where applicable.
    identity_patch: dict[str, Any] = {
        "primary_handle": ident.get("primary_handle"),
        "platform": ident.get("platform") or "instagram",
        "env": env,
    }
    has_identity_patch = False
    if body.primary_email and body.primary_email.strip():
        identity_patch["primary_email"] = body.primary_email.strip()
        has_identity_patch = True
    if body.display_name and body.display_name.strip():
        identity_patch["display_name"] = body.display_name.strip()
        has_identity_patch = True
    if addr_obj is not None:
        identity_patch["default_shipping_address"] = addr_obj
        has_identity_patch = True
    if has_identity_patch:
        try:
            await bridge.upsert_identity(identity_patch)
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        touched.append("identity_columns")

    # 3) Write identity.* facts (full_name / phone).
    identity_facts: dict[str, Any] = {}
    if body.full_name and body.full_name.strip():
        identity_facts["identity.full_name"] = body.full_name.strip()
    if body.phone and body.phone.strip():
        identity_facts["identity.phone"] = body.phone.strip()

    # 4) Write fulfillment.shipping_address fact (campaign-scoped) when
    # a structured object was supplied.
    fulfillment_facts: dict[str, Any] = {}
    if addr_obj is not None:
        fulfillment_facts["fulfillment.shipping_address"] = addr_obj
        # Mark address_collected so the shipping-intake skill skips re-asking.
        fulfillment_facts["fulfillment.address_collected"] = True

    if identity_facts or fulfillment_facts:
        namespaces: dict[str, dict[str, Any]] = {}
        if identity_facts:
            namespaces["identity"] = identity_facts
        if fulfillment_facts:
            namespaces["fulfillment"] = fulfillment_facts
        try:
            await bridge.write_facts_multi(
                body.identity_id,
                {
                    "campaign_id": cid,
                    "namespaces": namespaces,
                    "source": f"console:{user['email']}",
                    "env": env,
                },
            )
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if identity_facts:
            touched.append("identity_facts")
        if fulfillment_facts:
            touched.append("fulfillment_facts")

    if not touched:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no fields supplied; pass at least one of full_name / phone / primary_email / display_name / shipping_address",
        )

    write_audit(
        conn, actor_user_id=user["id"],
        action="contract.fill_blockers", target=campaign_id,
        payload={
            "identity_id": body.identity_id,
            "env": env,
            "touched": touched,
            "fields": [
                k for k in (
                    "full_name", "phone", "primary_email", "display_name",
                    "shipping_address",
                ) if getattr(body, k, None) is not None
            ],
        },
    )
    return {
        "ok": True,
        "identity_id": body.identity_id,
        "touched": touched,
    }


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
