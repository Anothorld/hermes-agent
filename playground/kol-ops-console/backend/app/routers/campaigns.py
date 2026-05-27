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
import os
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..bridge_runtime import ensure_gateway_bridge_key
from ..campaign_config_sync import (
    assert_campaign_config_complete,
    build_campaign_config_upsert_body,
)
from ..campaign_locks import campaign_lock
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..discovery_gate import (
    REDISCOVERY_INSTRUCTIONS,
    _count_uncontacted_candidates,
    _trigger_rediscover_internal,
)
from ..gateway_client import RUNNING_STATES, TERMINAL_STATES, GatewayClient, GatewayError
from ..run_registry import (
    INFLIGHT_TTL_SECONDS,
    get_inflight_run,
    list_recent_runs,
    list_runs_for_campaign,
    merge_legacy_run_id,
    register_run,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_REPO_ROOT = str(Path(__file__).resolve().parents[5])
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
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- For search_files/read_file/write_file/patch, use repo-relative\n"
    "  paths like `plugins/kol-ops-bridge` or absolute paths under\n"
    f"  `{_REPO_ROOT}/`.\n"
    "- Do NOT prefix file-tool paths with `./agent_prj/hermes-agent/`.\n"
    "- For terminal/Python execution, use absolute script paths.\n"
    "- ALL CAL writes/reads go through the deterministic CLI; never\n"
    "  hand-craft curl/PUT/POST. Single entry point:\n"
    f"    python {_REPO_ROOT}/plugins/kol-ops-bridge/\n"
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
    "\n"
    "   ITERATION CONTRACT — HARD QUANTITY FLOOR (read carefully):\n"
    "   - `discovery_target_count` is a HARD FLOOR on persisted candidates,\n"
    "     not a soft target. Only `add-candidate` rows present in\n"
    "     `list-candidates` count toward the floor; disqualifying a profile\n"
    "     (off-niche, audience too small, no contact) does NOT count.\n"
    "   - Budget yourself up to MAX(50, discovery_target_count * 4) profile\n"
    "     visits per pass. Try at least 3 distinct keyword angles before\n"
    "     considering yourself blocked.\n"
    "   - Stopping short is a FAILURE STATE. The console runs a post-\n"
    "     terminal quantity gate: if persisted candidates < target floor\n"
    "     AND auto-retry budget remains, the backend AUTO-FIRES another\n"
    "     /rediscover for this campaign_id (up to 5 auto-retries total =\n"
    "     6 runs max). After that, the operator gets a\n"
    "     `discovery_floor_unmet` escalation. Therefore: finishing partial\n"
    "     is acceptable ONLY when truly blocked (rate limits, niche\n"
    "     exhausted, bridge/gateway down).\n"
    "   - When you stop short you MUST include in the final answer the\n"
    "     following two structured lines so the backend can decide between\n"
    "     auto-retry and early escalation:\n"
    "       floor_unmet_reason: <one-sentence why>\n"
    "       attempted_angles:\n"
    "         - <keyword/angle 1>\n"
    "         - <keyword/angle 2>\n"
    "         - <keyword/angle 3>\n"
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
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- For search_files/read_file/write_file/patch, use repo-relative\n"
    "  paths like `plugins/kol-ops-bridge` or absolute paths under\n"
    f"  `{_REPO_ROOT}/`.\n"
    "- Do NOT prefix file-tool paths with `./agent_prj/hermes-agent/`.\n"
    "- For terminal/Python execution, use absolute script paths.\n"
    "- ALL CAL writes/reads go through the deterministic CLI; never\n"
    "  hand-craft curl/PUT/POST. Single entry point:\n"
    f"    python {_REPO_ROOT}/plugins/kol-ops-bridge/\n"
    "      scripts/kol_bridge_tool.py <cmd> --env <env> [--campaign-id <id>] ...\n"
    "- The bridge CLI is data-access only. There is NO `run-skill`,\n"
    "  `execute-skill`, or child-skill-runner subcommand. \"Invoke skill\n"
    "  X\" ALWAYS means: you (the executing LLM) read\n"
    f"  `{_REPO_ROOT}/skills/social-media/X/SKILL.md` and follow its\n"
    "  Procedure section yourself, using kol_bridge_tool.py for any CAL\n"
    "  reads/writes the SKILL.md instructs. Do NOT hunt for a CLI runner.\n"
    "\n"
    "## Pipeline (run in order, do NOT skip)\n"
    "0. ACK the trigger. For each approved identity, write one event via\n"
    "   `write-event --identity-id <id> --campaign-id <id> --env <env>\n"
    "   --event-type shortlist_approval_received --actor <approval-actor\n"
    "   from the input brief>` (payload may carry approved_at, source).\n"
    "   This anchors the run on the timeline and lets downstream tooling\n"
    "   detect repeat shortlist clicks.\n"
    "1. Treat this run as the operator gate after candidate-pool approval.\n"
    "   Do NOT run discovery again and do NOT wait for a new inbound reply.\n"
    "2. Read campaign_config, campaign candidates, and dispatch-context for\n"
    "   every approved identity ID in the input. Ignore unapproved IDs.\n"
    "3. Determine outreach path from CAL, not from prose: prefer\n"
    "   campaign_candidates.relationship_status / identity.outreach_path; if\n"
    "   absent, use relationship.total_collabs (0 => cold, >0 =>\n"
    "   reengagement). If last_outcome is disputed/content_failed/aborted,\n"
    "   open an escalation and do not draft for that KOL.\n"
    "4. ENRICHMENT (before any draft skill). For each approved identity\n"
    "   whose `primary_email` (from get-identity) is empty or null:\n"
    "   a) invoke `kol-email-discovery` with identity_id, env, campaign_id.\n"
    "      The skill returns `{found: true, email, source, tier, ...}` on\n"
    "      hit (and has already persisted `primary_email` + provenance\n"
    "      facts) or `{found: false, tried: [...]}` on miss.\n"
    "   b) On hit: continue to step 5 for this identity.\n"
    "   c) On miss: do NOT call `kol-cold-outreach` /\n"
    "      `kol-reengagement-outreach` for this identity. Open an\n"
    "      escalation via the bridge CLI with\n"
    "      reason=`contact_email_not_found`, identity_id, campaign_id,\n"
    "      and `question_to_operator` containing the `tried` list verbatim\n"
    "      so the operator can see what was checked. Continue to the next\n"
    "      approved identity — never invent an address, never invoke a\n"
    "      draft skill without a verified email.\n"
    "   Identities that already had a non-empty `primary_email` skip\n"
    "   enrichment and proceed directly to step 5.\n"
    "5. IDEMPOTENCY GATE (run BEFORE any draft skill invocation). For\n"
    "   each approved identity, call `get-facts --identity-id <id>\n"
    "   --campaign-id <id> --env <env>` and check:\n"
    "   - `offer.outreach_draft_ready == true`, OR\n"
    "   - `approval.reply_draft` exists with decision in\n"
    "     {pending, approved}.\n"
    "   If either is true, SKIP the draft skill for this identity. Add\n"
    "   it to the per-KOL summary under `already_drafted` (with the\n"
    "   existing draft's `source` and `decided_at` if available) and do\n"
    "   NOT open an escalation. Re-drafting a KOL whose draft is already\n"
    "   pending or approved would overwrite the operator's queue and is\n"
    "   a data-pollution bug, not a recoverable case. Only identities\n"
    "   that pass this gate proceed to step 6.\n"
    "6. For each approved cold prospect WITH a verified email that passed\n"
    "   step 5, invoke `kol-cold-outreach` with identity_id, campaign_id,\n"
    "   env. For each approved safe repeat KOL WITH a verified email that\n"
    "   passed step 5, invoke `kol-reengagement-outreach`. (\"Invoke\"\n"
    "   means: read the SKILL.md and execute its Procedure yourself — see\n"
    "   Runtime contract above.) Each child skill must return a draft\n"
    "   envelope. Do NOT write `offer.outreach_sent=true` unless an email\n"
    "   was actually sent; draft-only work writes draft-ready facts and\n"
    "   pending approval records instead.\n"
    "7. Persist every returned initial outreach draft back to CAL before\n"
    "   reporting success. Write both:\n"
    "   a) `write-event --event-type kol_initial_outreach_draft_ready` with\n"
    "      payload containing child_skill, identity_id, and draft envelope;\n"
    "   b) `write-facts-multi` with `approval.reply_draft={decision:\n"
    "      \"pending\", kind:\"initial_outreach\", child_skill, draft}`.\n"
    "   This is the durable Web approval queue.\n"
    "8. In TEST mode, any Gmail draft/test target must be test_mode_to from\n"
    "   campaign_config. Never send mail without another explicit operator\n"
    "   action. If Gmail draft creation is unavailable, the CAL\n"
    "   `approval.reply_draft` record is still required.\n"
    "9. Stop after draft records or escalations are persisted and summarize\n"
    "   per approved KOL: enrichment outcome (hit/miss/skipped),\n"
    "   idempotency outcome (already_drafted | proceeded), draft or\n"
    "   escalation status, and (for misses) the `tried` list.\n"
    "\n"
    "## Failure handling\n"
    "- If bridge auth fails, stop and report the missing\n"
    "  HERMES_KOL_OPS_BRIDGE_KEY; do not bypass CAL.\n"
    "- If required product facts are missing, open an escalation through\n"
    "  the bridge CLI instead of inventing values.\n"
    "- Missing `primary_email` is NOT a hard failure: it is the trigger\n"
    "  for step 4 (kol-email-discovery). Only if step 4 returns\n"
    "  `found: false` do you open a `contact_email_not_found` escalation\n"
    "  and skip the draft skill for that identity.\n"
    "- Never invent an email address. Never invoke a draft skill for an\n"
    "  identity that does not have a verified `primary_email` after\n"
    "  step 4.\n"
    "- Distinguish two failure modes for child-skill draft generation;\n"
    "  picking the wrong reason pollutes the operator queue and hides\n"
    "  the real cause:\n"
    "  (i)  Skill DID execute and returned no draft envelope (or a\n"
    "       malformed envelope). Open escalation with reason\n"
    "       `initial_outreach_draft_missing` and put the skill's actual\n"
    "       return value (truncated) into `question_to_operator`.\n"
    "  (ii) Skill could NOT be executed at all — e.g. SKILL.md unreadable,\n"
    "       tooling/runner confusion, transient bridge error during a\n"
    "       SKILL.md-dictated CAL call. Open escalation with reason\n"
    "       `child_skill_invocation_failed` and put the concrete failure\n"
    "       mode (\"could not locate SKILL.md\", \"bridge 503 on\n"
    "       get-facts\", \"argparse rejected subcommand X\", etc.) into\n"
    "       `question_to_operator`. NEVER fall back to\n"
    "       `initial_outreach_draft_missing` for this case.\n"
)


# ``REDISCOVERY_INSTRUCTIONS`` lives in ``app/discovery_gate.py`` so the
# post-terminal quantity-gate hook can reuse it without importing this
# routers module.


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


_VALID_BROWSER_MODES = {"cloud", "local-chrome"}


def _resolve_browser_mode() -> str:
    """Read the operator-set browser backend mode from env.

    Distinct from the campaign ``mode:`` field (which carries LIVE/TEST env).
    Controls whether the kol-discovery skill applies cloud or local-chrome
    safety rules (pacing, forbidden actions, per-run caps).

    Defaults to ``local-chrome`` because Browser Use cloud Browser-Use cloud
    profiles routinely lose IG login state and Browser Use 5xx outages have
    been observed in prod; the user's debug-Chrome profile is the reliable
    path for IG-heavy work. Set ``KOL_BROWSER_MODE=cloud`` to opt back into
    the cloud backend for an individual campaign run.
    """
    raw = (os.environ.get("KOL_BROWSER_MODE") or "").strip().lower()
    if raw in _VALID_BROWSER_MODES:
        return raw
    return "local-chrome"


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
        f"browser_mode: {_resolve_browser_mode()}",
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


# NOTE: ``_compose_rediscover_brief`` has moved to ``app/discovery_gate.py``
# so the post-terminal auto-retry hook can use it without importing this
# routers module. The /rediscover HTTP endpoint below now uses the
# ``_trigger_rediscover_internal`` helper from the same module.


# Cap operator-supplied free-text fields to keep upstream token cost
# predictable.  16k chars ~ 4k tokens; 64k chars ~ 16k tokens.
_MAX_BRIEF_EXTRA = 16_000
_MAX_PRODUCT_PITCH = 64_000

# Matches SKU / model codes like "SEB800", "SEB-8008", "TS8319",
# "POV-RUG-04". Cold-outreach refuses to put these in the email body;
# the matching guard lives in kol-cold-outreach/SKILL.md Step 2b. We
# enforce the same shape here so a friendly `product_display_name` can
# never silently default to a SKU.
_SKU_CODE_RE = re.compile(r"^[A-Z]{2,5}[\- ]?\d{3,5}[A-Z0-9]*$")


def _validate_product_display_name(
    value: str, *, sku: str | None = None, campaign_id: str | None = None,
) -> str:
    """Reject SKU-shaped strings, the bound SKU, or the campaign id.

    The whole reason this field exists is to give the cold-outreach skill
    a human-friendly product reference. Letting it default to a SKU
    re-introduces the exact leak the field was added to prevent.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("product_display_name must be non-empty")
    if len(stripped) < 2 or len(stripped) > 80:
        raise ValueError("product_display_name must be 2-80 characters")
    if _SKU_CODE_RE.match(stripped):
        raise ValueError(
            f"product_display_name '{stripped}' looks like a SKU/model code; "
            "use a human-friendly name (e.g. 'the new media console')"
        )
    if sku and stripped.casefold() == sku.casefold():
        raise ValueError(
            "product_display_name must not equal the SKU; "
            "use a human-friendly name instead"
        )
    if campaign_id and stripped.casefold() == campaign_id.casefold():
        raise ValueError(
            "product_display_name must not equal the campaign_id; "
            "use a human-friendly name instead"
        )
    return stripped


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
    product_display_name: str = Field(
        min_length=2,
        max_length=80,
        description=(
            "Operator-friendly product name used in cold-outreach emails "
            "(e.g. 'the new media console'). Required to prevent SKU codes "
            "from leaking into KOL-facing copy. Must not be a SKU-shaped "
            "string and must not equal product_sku or the campaign_id."
        ),
    )
    budget_per_kol: float = Field(gt=0)
    absolute_floor: float = Field(gt=0)
    budget_total: float = Field(gt=0)
    headcount_target: int = Field(ge=1, le=200)
    test_mode_to: str | None = None
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

    @model_validator(mode="after")
    def _require_test_mode_to_for_test_env(self) -> "StartCampaignBody":
        if self.env == "TEST" and not (self.test_mode_to and self.test_mode_to.strip()):
            raise ValueError(
                "test_mode_to is required when env=TEST (recipient for test-mode emails)"
            )
        return self

    @model_validator(mode="after")
    def _validate_product_display_name(self) -> "StartCampaignBody":
        self.product_display_name = _validate_product_display_name(
            self.product_display_name, sku=self.product_sku,
        )
        return self


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
    ensure_gateway_bridge_key()

    # Seed campaign metadata in the bridge first so downstream skills can
    # find the campaign row before discovery starts writing candidates.
    selected_variants = _selected_variants(product, body.product_variant_ids)
    upsert_body = build_campaign_config_upsert_body(
        product=product,
        body=body,
        selected_variants=selected_variants,
        sku_ref=sku_ref,
    )
    try:
        await bridge.upsert_campaign(campaign_id, upsert_body)
    except BridgeError as exc:
        # Abort the launch. CAL is the canonical product-config store
        # the downstream skills read from; a campaign created with the
        # console row present but CAL row empty looks "running" but
        # every downstream draft attempt will escalate. Better to
        # refuse the launch and let the operator retry once CAL is
        # back. The bridge_client already retried once for transient
        # transport errors, so reaching here means a deterministic
        # failure (auth/schema) or sustained outage.
        write_audit(
            conn,
            actor_user_id=user["id"],
            action="campaign.upsert_failed",
            target=campaign_id,
            payload={"error": str(exc)},
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {
                "code": "cal_upsert_failed",
                "message": (
                    "cannot write campaign_config to CAL — campaign "
                    "launch aborted. Check the bridge is reachable, "
                    "then retry."
                ),
                "retry_after_seconds": 10,
                "bridge_error": str(exc),
            },
        ) from exc

    # Snapshot baseline candidate count BEFORE starting the gateway run so
    # the post-terminal quantity gate has authoritative values to compare
    # against. ``baseline`` and ``target_floor`` use the uncontacted-pool
    # metric (excludes rejected/archived AND selected_for_outreach) so the
    # gate measures fresh, contactable leads — not KOLs already dispatched
    # to outreach. Re-runs (force=true) of a campaign with prior pool must
    # measure those rows as baseline, not as part of this run's yield.
    discovery_target = body.discovery_target_count or max(
        body.headcount_target * 3, body.headcount_target + 5
    )
    try:
        pre_candidates = await bridge.list_candidates(campaign_id, env=body.env)
    except BridgeError:
        pre_candidates = []
    baseline_count = _count_uncontacted_candidates(pre_candidates)
    target_floor = baseline_count + discovery_target

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
    # gate_run_id = run_id so the post-terminal gate watches THIS launch
    # run specifically; later approve-driven outreach runs (which overwrite
    # ``run_id``) won't accidentally trigger the discovery gate.
    conn.execute(
        """INSERT INTO product_campaigns
                         (sku, campaign_id, env, run_id, test_mode_to, started_at,
                          started_by_user_id, status, target_floor,
                          baseline_candidate_count, retry_count, floor_unmet_reason,
                          gate_run_id, diagnostics_history)
                     VALUES (?,?,?,?,?,?,?, 'running', ?, ?, 0, NULL, ?, '[]')
           ON CONFLICT(campaign_id, env) DO UPDATE SET
             run_id=excluded.run_id,
                         test_mode_to=excluded.test_mode_to,
             started_at=excluded.started_at,
             started_by_user_id=excluded.started_by_user_id,
             status='running',
             target_floor=excluded.target_floor,
             baseline_candidate_count=excluded.baseline_candidate_count,
             retry_count=0,
             floor_unmet_reason=NULL,
             gate_run_id=excluded.gate_run_id,
             diagnostics_history='[]'""",
                (product["sku"], campaign_id, body.env, run_id, body.test_mode_to, now,
                 user["id"], target_floor, baseline_count, run_id),
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


class RediscoverBody(BaseModel):
    """Body for the operator's "discover N more KOLs" click."""

    additional_count: int = Field(
        ge=1,
        le=200,
        description="How many NEW candidates to add to the pool on top of "
        "whatever is already persisted in CAL.",
    )
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")


async def _campaign_run_in_flight(
    conn: sqlite3.Connection,
    gateway: GatewayClient,
    *,
    campaign_id: str,
    env: str,
) -> tuple[bool, str | None, str | None]:
    """Return ``(in_flight, run_id, run_state)`` for the campaign's latest run.

    "In flight" means EITHER the latest tracked run (``run_id``) is in a
    non-terminal gateway state OR the campaign has an open discovery
    gate (``gate_run_id`` is set, regardless of the gate run's gateway
    state — see ``products._sync_run_states`` for why we treat the
    whole gate cycle as in-flight). Both signals matter because:

    * approve-driven outreach uses ``run_id`` (so a fresh approve sees
      the previous approve's outreach run as in-flight);
    * a rediscover that just reached terminal but whose auto-retry has
      not yet started is still semantically "discovery in progress" —
      approve must block until ``evaluate_gate_after_terminal`` clears
      the gate pointer.

    Gateway-terminal runs are NOT auto-flipped here (that is
    ``_sync_run_states``' job); we just treat them as not-in-flight for
    the ``run_id`` half of the check so a new operator action can
    proceed against a stale ``status='running'`` row.
    """
    row = conn.execute(
        "SELECT run_id, status, gate_run_id FROM product_campaigns "
        "WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    if row is None:
        return False, None, None

    gate_run_id = row["gate_run_id"] if "gate_run_id" in row.keys() else None
    if gate_run_id:
        return True, row["run_id"], "gate_active"

    if row["status"] != "running" or not row["run_id"]:
        return False, row["run_id"], None
    try:
        info = await gateway.get_run(row["run_id"])
    except GatewayError:
        # Unreachable gateway — be conservative and treat as in-flight.
        return True, row["run_id"], "unknown"
    if info is None:
        # Gateway evicted the run -> definitely not in flight anymore.
        return False, row["run_id"], None
    state = str(info.get("status") or "").lower()
    return state in RUNNING_STATES, row["run_id"], state or None


@router.post("/{campaign_id}/rediscover")
async def rediscover(
    campaign_id: str,
    body: RediscoverBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict[str, Any]:
    """Spawn a discovery-only agent run for an existing campaign.

    Reuses the campaign_config already persisted in CAL — does NOT call
    upsert-campaign. Tells the agent to find ``additional_count`` candidates
    that are NOT in the current candidate pool. Conflict semantics:

    * If a campaign run is already in flight (status='running' on the
      console row AND gateway reports a non-terminal state), return 409
      ``campaign_run_in_flight``. We never auto-stop the running agent —
      the operator must Stop+close it first.
    * If another rediscover was fired in the last ``INFLIGHT_TTL_SECONDS``,
      return 409 ``rediscover_inflight``. Cheap defense against rapid
      double-clicks given the agent's slow startup.
    """
    row = conn.execute(
        "SELECT sku, test_mode_to FROM product_campaigns "
        "WHERE campaign_id=? AND env=?",
        (campaign_id, body.env),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not tracked")

    # Serialize against the discovery gate's auto-retry hook and any
    # concurrent operator action (approve, second rediscover click) so
    # the "check → start_run → register_run → UPDATE" sequence is
    # atomic per (env, campaign_id).
    lock = await campaign_lock(body.env, campaign_id)
    async with lock:
        in_flight, current_run_id, current_state = await _campaign_run_in_flight(
            conn, gateway, campaign_id=campaign_id, env=body.env,
        )
        if in_flight:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "campaign_run_in_flight",
                    "message": (
                        "another agent run is still active for this campaign — "
                        "Stop + close it (or wait for terminal state) before "
                        "re-discovering"
                    ),
                    "run_id": current_run_id,
                    "run_state": current_state,
                },
            )

        product = conn.execute(
            "SELECT sku, name, url, tags_json, notes, pitch_md, selling_points, "
            "variants_json FROM products WHERE sku=?",
            (row["sku"],),
        ).fetchone()
        if product is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "campaign's product no longer exists in the catalog",
            )

        try:
            helper_out = await _trigger_rediscover_internal(
                bridge=bridge,
                gateway=gateway,
                conn=conn,
                product=product,
                campaign_id=campaign_id,
                env=body.env,
                additional_count=body.additional_count,
                test_mode_to_override=None,
                current_test_mode_to=row["test_mode_to"],
                rediscovery_instructions=REDISCOVERY_INSTRUCTIONS,
                actor=user,
                is_auto_retry=False,
            )
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    if not helper_out.get("ok"):
        skipped = helper_out.get("skipped")
        if skipped == "rediscover_inflight":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "rediscover_inflight",
                    "message": (
                        f"a rediscover was started in the last "
                        f"{INFLIGHT_TTL_SECONDS}s — wait before retriggering"
                    ),
                    "run_id": helper_out.get("run_id"),
                    "started_at": helper_out.get("started_at"),
                },
            )
        if skipped == "test_mode_to_missing":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "test_mode_to missing for TEST campaign; restart the campaign or "
                "fix product_campaigns.test_mode_to",
            )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"rediscover failed: {skipped or 'unknown'}",
        )

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "env": body.env,
        "run_id": helper_out.get("run_id"),
        "additional_count": body.additional_count,
        "excluded_handle_count": helper_out.get("excluded_handle_count", 0),
    }


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

    # Clear ``gate_run_id`` on close so the discovery-gate watchdog does
    # not fire an auto-retry against a run the operator just stopped.
    conn.execute(
        "UPDATE product_campaigns SET status=?, gate_run_id=NULL "
        "WHERE campaign_id=? AND env=?",
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


def _session_file_by_sid(session_id: str) -> Path:
    """Hermes writes one JSON file per session keyed by the full
    session_id (including the namespace prefix and env). This resolver
    lets us read the on-disk transcript for any session — outreach,
    draft-refine, email-discover — given just its session_id."""
    return _KOL_ORCHESTRATOR_SESSIONS / f"session_{session_id}.json"


def _tool_call_label(call: dict[str, Any]) -> str:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = fn.get("name") or call.get("name") or "tool"
    args = fn.get("arguments") or call.get("arguments") or ""
    return f"{name}({ _clip_text(args, 1200) })"


def _parse_session_file(path: Path, limit: int) -> list[dict[str, Any]] | None:
    """Read a hermes session JSON file and project it to transcript rows.

    Returns ``None`` when the file does not exist (caller can decide to
    surface "no history" vs fall back to another source). Returns ``[]``
    when the file exists but contains no usable messages.
    """
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


def _transcript_items(campaign_id: str, env: str, limit: int) -> list[dict[str, Any]] | None:
    return _parse_session_file(_session_file(campaign_id, env), limit)


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


def _ts_from_unix(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        return _dt.datetime.fromtimestamp(value, _dt.timezone.utc).isoformat(timespec="seconds")
    return ""


async def _gateway_completed_snapshot(
    *,
    campaign_id: str,
    env: str,
    conn: sqlite3.Connection,
    gateway: GatewayClient,
) -> list[dict[str, Any]] | None:
    """Synthesize a transcript from completed gateway runs.

    Called when the session-file snapshot is empty. Gateway evicts the
    per-run SSE ring buffer after a run completes, but ``GET /v1/runs/{id}``
    still returns the run object (status + ``output``) for ~1h. For each
    registered run in terminal state we emit a done/error marker + an
    assistant row holding the run's final output text. Skips runs the
    gateway has already evicted (None response).
    """
    legacy_run_id = _latest_campaign_run_id(conn, campaign_id, env)
    merge_legacy_run_id(
        conn,
        campaign_id=campaign_id,
        env=env,
        legacy_run_id=legacy_run_id,
        legacy_kind="outreach",
    )
    runs = list_runs_for_campaign(conn, campaign_id=campaign_id, env=env, limit=20)
    if not runs:
        return None
    items: list[dict[str, Any]] = []
    for r in reversed(runs):
        try:
            run = await gateway.get_run(r["run_id"])
        except GatewayError:
            run = None
        if not isinstance(run, dict):
            continue
        status_val = run.get("status")
        if status_val not in TERMINAL_STATES:
            continue
        ts = _ts_from_unix(run.get("updated_at")) or r.get("started_at") or ""
        kind_label = r.get("kind", "run")
        items.append({
            "ts": ts,
            "kind": "done" if status_val == "completed" else "error",
            "label": f"{kind_label} · {status_val}",
            "message": f"run {r['run_id'][:12]} · {status_val}",
        })
        output_raw = run.get("output")
        if isinstance(output_raw, str) and output_raw.strip():
            message = output_raw
        elif isinstance(output_raw, (dict, list)):
            message = json.dumps(output_raw, ensure_ascii=False)
        else:
            message = ""
        if message:
            items.append({
                "ts": ts,
                "kind": "assistant",
                "label": kind_label,
                "message": _clip_text(message, 4000),
            })
    return items or None


@router.get("/agent-sessions")
async def agent_sessions(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Recent runs grouped by ``session_id`` — feeds the global Agent
    Session Dock.

    ``session_id`` clusters related workflows (one
    ``kol-campaign:{env}:{cid}`` covers outreach + reply + refine for a
    campaign; one ``kol-email-discover:{env}:{identity_id}`` covers all
    discovery runs for an identity). Rows with NULL ``session_id`` are
    surfaced as their own pseudo-session keyed ``run:{run_id}`` so each
    appears as a separate row.
    """
    runs = list_recent_runs(conn, env=env, limit=limit)
    groups: dict[str, dict] = {}
    for r in runs:
        sid = r.get("session_id") or f"run:{r['run_id']}"
        g = groups.get(sid)
        if g is None:
            g = {
                "session_id": sid,
                "campaign_id": r["campaign_id"],
                "kinds": [],
                "runs": [],
                "first_started_at": r["started_at"],
                "last_activity_at": r["started_at"],
                "open": False,
            }
            groups[sid] = g
        else:
            if r["started_at"] > g["last_activity_at"]:
                g["last_activity_at"] = r["started_at"]
                g["campaign_id"] = r["campaign_id"]
            if r["started_at"] < g["first_started_at"]:
                g["first_started_at"] = r["started_at"]
        kind = r["kind"]
        if kind not in g["kinds"]:
            g["kinds"].append(kind)
        g["runs"].append({
            "run_id": r["run_id"],
            "kind": kind,
            "started_at": r["started_at"],
            "ended_at": r.get("ended_at"),
        })
        if not r.get("ended_at"):
            g["open"] = True
    sessions = sorted(
        groups.values(),
        key=lambda g: g["last_activity_at"],
        reverse=True,
    )
    return {"env": env, "sessions": sessions}


@router.get("/agent-sessions/{session_id}/log")
async def agent_session_log(
    session_id: str,
    _: Annotated[dict, Depends(current_user)],
    env: str = Query("TEST", pattern="^(LIVE|TEST)$"),
    limit: int = Query(160, ge=1, le=500),
) -> dict:
    """Session-scoped historical transcript.

    Reads ``~/.hermes/profiles/kol-orchestrator/sessions/session_{sid}.json``
    directly. Hermes keys these files by the full session_id (matching
    what ``/campaigns/agent-sessions`` surfaces), so this works for
    closed sessions even after the gateway has evicted its per-run
    event ring buffers — the rich step-by-step history is what the dock
    needs to show "this finished run, what did the agent do".

    Items are returned without ``run_id`` attribution because the file
    is a single time-ordered conversation, not a per-run feed. The
    consuming UI knows they are session-scoped (came from this endpoint)
    and renders them directly rather than filtering by run_id.

    Returns ``items=[]`` when no file exists — pseudo-sessions
    (``run:{run_id}``) and namespaces whose runtime doesn't persist a
    session file land here.

    The caller URL-encodes the session_id (it contains colons like
    ``kol-campaign:LIVE:CID-42``); FastAPI decodes it before binding.
    Containment guard: any embedded path separator is rejected so the
    session_id can't traverse out of the sessions directory.
    """
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid session_id")
    items = _parse_session_file(_session_file_by_sid(session_id), limit) or []
    return {"session_id": session_id, "env": env, "items": items}


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
    if transcript:
        return {
            "campaign_id": campaign_id,
            "env": env,
            "source": "session",
            "items": transcript[-limit:],
        }
    completed = await _gateway_completed_snapshot(
        campaign_id=campaign_id, env=env, conn=conn, gateway=gateway,
    )
    if completed:
        return {
            "campaign_id": campaign_id,
            "env": env,
            "source": "gateway-completed",
            "items": completed[-limit:],
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


_GATEWAY_TERMINAL_TO_FRAME = {
    "completed": "run.completed",
    "failed": "run.failed",
    "cancelled": "run.cancelled",
}


async def _emit_synthetic_terminal(
    *,
    run_id: str,
    kind: str,
    run_obj: dict[str, Any],
    out_queue: asyncio.Queue,
) -> None:
    """Project a terminal gateway run record into transcript-shaped frames.

    Gateway evicts the per-run SSE event ring buffer the instant a run
    reaches terminal state, but ``GET /v1/runs/{id}`` still returns the
    run object (incl. ``output`` text) for ~1h. We use that to emit one
    wrapped ``run.completed/failed/cancelled`` frame so the FE
    transcript at least shows that the run finished + the final
    assistant output, even when the FE subscribes after the run ended.
    Without this synthesis, late subscribers get an empty stream and a
    silent ``run.evicted`` — operators read this as "the run was lost".
    """
    status_val = str(run_obj.get("status") or "").lower()
    inner_event = _GATEWAY_TERMINAL_TO_FRAME.get(status_val) or "run.completed"
    output = run_obj.get("output")
    if isinstance(output, (dict, list)):
        try:
            output_text = json.dumps(output, ensure_ascii=False)
        except (TypeError, ValueError):
            output_text = ""
    else:
        output_text = str(output or "")
    ts_val = run_obj.get("updated_at") or run_obj.get("started_at")
    payload = {
        "status": status_val or "completed",
        "output": output_text[:_MAX_TRANSCRIPT_CHARS] if output_text else "",
        "timestamp": ts_val,
        "synthesized": True,
        "error": run_obj.get("error"),
    }
    await out_queue.put(_sse_frame(
        inner_event,
        {"run_id": run_id, "kind": kind, "event": inner_event,
         "payload": payload},
    ))


async def _proxy_run_events(
    *,
    run_id: str,
    kind: str,
    out_queue: asyncio.Queue,
    settings,
    stop_event: asyncio.Event,
    gateway: GatewayClient | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Subscribe to one gateway run's SSE feed and push frames into out_queue.

    Each gateway frame is rewrapped with ``run_id`` + ``kind`` so the
    frontend can label which run a line came from.

    Three behavioral details that the FE depends on:

    1. **Late-subscriber replay** — before opening the SSE stream we
       check whether the gateway run is already terminal. If yes,
       synthesize a ``run.completed/failed/cancelled`` frame from the
       run's ``output`` text and return. This rescues the common case
       where the operator opens the campaign page after a fast
       rediscover already finished (SSE ring buffer evicted → would
       otherwise show as an empty transcript).
    2. **ended_at write** — when the gateway run reaches terminal we
       call ``mark_run_ended`` so the registry's ``ended_at`` matches
       reality. Without this, the FE shows runs as "live" indefinitely
       because nothing else writes ``ended_at``.
    3. **Graceful exit** — quietly returns on gateway 404 (run already
       evicted) or connection failure; the aggregator keeps serving
       the remaining runs.
    """
    from ..run_registry import mark_run_ended

    # ── (1) Late-subscriber replay path ────────────────────────────────
    if gateway is not None:
        try:
            existing = await gateway.get_run(run_id)
        except GatewayError:
            existing = None
        if isinstance(existing, dict):
            existing_status = str(existing.get("status") or "").lower()
            if existing_status in TERMINAL_STATES:
                await _emit_synthetic_terminal(
                    run_id=run_id, kind=kind,
                    run_obj=existing, out_queue=out_queue,
                )
                if conn is not None:
                    try:
                        mark_run_ended(conn, run_id=run_id)
                    except sqlite3.Error:
                        pass
                await out_queue.put(_sse_frame(
                    "run.closed", {"run_id": run_id, "kind": kind}
                ))
                return

    # ── (2) Live SSE proxy ─────────────────────────────────────────────
    url = f"{settings.gateway_base.rstrip('/')}/v1/runs/{run_id}/events"
    headers: dict[str, str] = {"Accept": "text/event-stream"}
    if settings.gateway_key:
        headers["Authorization"] = f"Bearer {settings.gateway_key}"
    saw_terminal_inner_event = False
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 404:
                    # Race: run completed between the get_run() probe
                    # above and our SSE subscribe. Try once more to
                    # synthesize from a fresh get_run.
                    late: dict[str, Any] | None = None
                    if gateway is not None:
                        try:
                            late = await gateway.get_run(run_id)
                        except GatewayError:
                            late = None
                    if isinstance(late, dict) and str(
                        late.get("status") or ""
                    ).lower() in TERMINAL_STATES:
                        await _emit_synthetic_terminal(
                            run_id=run_id, kind=kind,
                            run_obj=late, out_queue=out_queue,
                        )
                        saw_terminal_inner_event = True
                    else:
                        await out_queue.put(_sse_frame(
                            "run.evicted",
                            {"run_id": run_id, "kind": kind},
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
                            # Fallback: if the upstream SSE source did not
                            # emit an `event:` header (older gateway builds
                            # before the header was added, or any
                            # non-conforming producer), recover the event
                            # name from the payload body's "event" field.
                            # Without this fallback, every frame would be
                            # tagged "message" and the FE — which routes
                            # by event name — would silently drop it.
                            if event_name == "message" and isinstance(payload_obj, dict):
                                inner = payload_obj.get("event")
                                if isinstance(inner, str) and inner:
                                    event_name = inner
                            await out_queue.put(_sse_frame(
                                event_name if event_name != "message" else "run.event",
                                {"run_id": run_id, "kind": kind,
                                 "event": event_name,
                                 "payload": payload_obj},
                            ))
                            if event_name in {
                                "run.completed", "run.failed", "run.cancelled",
                            }:
                                saw_terminal_inner_event = True
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
        # Write ended_at if the gateway has reported a terminal state
        # (either via SSE event or via a probe get_run after the stream
        # closed silently). Without this, the registry's ended_at
        # stays NULL and the FE renders the run as live forever.
        if conn is not None:
            try:
                if saw_terminal_inner_event:
                    mark_run_ended(conn, run_id=run_id)
                elif gateway is not None:
                    try:
                        last = await gateway.get_run(run_id)
                    except GatewayError:
                        last = None
                    if isinstance(last, dict) and str(
                        last.get("status") or ""
                    ).lower() in TERMINAL_STATES:
                        mark_run_ended(conn, run_id=run_id)
            except sqlite3.Error:
                pass
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
        if transcript:
            snapshot_items = transcript[-limit:]
        else:
            completed = await _gateway_completed_snapshot(
                campaign_id=campaign_id, env=env, conn=conn, gateway=gateway,
            )
            if completed:
                snapshot_items = completed[-limit:]

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
                out_queue=out_queue, settings=settings,
                stop_event=stop_event,
                gateway=gateway, conn=conn,
            ))
        try:
            # New-run discovery poll cadence: 1.5s. Approve-driven
            # outreach runs that follow a rediscover should show up in
            # the transcript within a couple of seconds, not 5+. The
            # poll is cheap (SELECT against a small indexed table).
            NEW_RUN_POLL_INTERVAL = 1.5
            keepalive_at = asyncio.get_event_loop().time() + 25.0
            poll_at = asyncio.get_event_loop().time() + NEW_RUN_POLL_INTERVAL
            while True:
                timeout = max(
                    0.25,
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
                                gateway=gateway, conn=conn,
                            ))
                            yield _sse_frame("run.added", {
                                "run_id": r["run_id"], "kind": r["kind"],
                                "started_at": r["started_at"],
                            })
                    poll_at = now + NEW_RUN_POLL_INTERVAL
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
            "candidate_status": row.get("candidate_status"),
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
        f"- Use {_REPO_ROOT}/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py.",
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
    """Approve candidates in CAL, then launch the post-approval agent run.

    Concurrency contract:

    * 409 ``campaign_run_in_flight`` if a rediscover, auto-retry, or
      previous approve outreach run is still working. Prevents truncating
      the candidate pool while the discovery quantity-gate is mid-cycle.
    * 409 ``approve_inflight`` if another approve fired within the last
      ``INFLIGHT_TTL_SECONDS``. Bare-minimum double-click defense.
    * Holds the per-campaign asyncio lock for the entire critical path
      so the discovery-gate auto-retry hook can't race against the
      approve-driven outreach run start.

    Does NOT touch ``gate_run_id`` — the discovery gate's pointer is
    invariant under approve; the row's ``run_id`` is overwritten to
    point at the post-approval outreach run for status/display purposes.
    """
    # Lock first; everything else (bridge calls, gateway call, row
    # update, audit) runs under the lock. Approve is rare enough that
    # holding the lock through a few bridge round-trips is fine.
    lock = await campaign_lock(body.env, campaign_id)
    async with lock:
        # ── (1) Block while another run is in flight ─────────────────
        in_flight, current_run_id, current_state = await _campaign_run_in_flight(
            conn, gateway, campaign_id=campaign_id, env=body.env,
        )
        if in_flight:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "campaign_run_in_flight",
                    "message": (
                        "another agent run is still active for this "
                        "campaign — wait for it to terminate (or Stop "
                        "+ close it) before approving"
                    ),
                    "run_id": current_run_id,
                    "run_state": current_state,
                },
            )

        # ── (2) TTL dedup on approve itself ──────────────────────────
        dedup_key = f"approve:{body.env}:{campaign_id}"
        inflight_approve = get_inflight_run(conn, dedup_key=dedup_key)
        if inflight_approve is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "approve_inflight",
                    "message": (
                        f"an approve was fired in the last "
                        f"{INFLIGHT_TTL_SECONDS}s — wait before retriggering"
                    ),
                    "run_id": inflight_approve["run_id"],
                    "started_at": inflight_approve["started_at"],
                },
            )

        selected = {
            h.strip().lstrip("@").lower()
            for h in body.selected_handles if h.strip()
        }
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
                handle = str(
                    ident.get("primary_handle") or row.get("primary_handle") or ""
                ).lstrip("@").lower()
                if handle in selected:
                    identity_ids.append(identity_id)
                    selected_rows.append({"identity_id": identity_id, "handle": handle})
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if selected and not identity_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "selected handles did not match any candidates",
            )
        campaign_row = conn.execute(
            "SELECT sku, test_mode_to FROM product_campaigns "
            "WHERE campaign_id=? AND env=?",
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
        ensure_gateway_bridge_key()
        campaign_update: dict[str, Any] = {"env": body.env}
        if campaign_row and campaign_row["sku"]:
            campaign_update["sku_whitelist"] = [campaign_row["sku"]]
        if test_mode_to:
            campaign_update["test_mode_to"] = test_mode_to
        try:
            await bridge.upsert_campaign(campaign_id, campaign_update)
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        # Idempotently run discovery → outreach routing before selecting.
        # The router is a no-op for candidates already past
        # candidate_status='discovered' (reported as skipped_already_routed),
        # but for any candidate still in 'discovered' it writes
        # identity.outreach_path facts → triggers recompute_goals →
        # creates the kol_goal_state.outreach row. Without this, an
        # operator who clicks approve before explicitly invoking the
        # router leaves the candidate with no goal_state row, and
        # get_goal_state defaults outreach to "inactive", which blocks
        # every downstream draft skill that gates on
        # goals.outreach.status == "active".
        try:
            await bridge.route_discovery(
                campaign_id,
                {"env": body.env, "selected_by": f"web:{user['email']}"},
            )
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
        # Approve overwrites ``run_id`` for status/display but MUST NOT
        # touch ``gate_run_id``; the discovery gate's pointer is owned
        # exclusively by ``_trigger_rediscover_internal`` /
        # ``evaluate_gate_after_terminal``.
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
                dedup_key=dedup_key,
            )
        write_audit(
            conn, actor_user_id=user["id"],
            action="campaign.approve_shortlist",
            target=campaign_id,
            payload={**payload, "selected_handles": body.selected_handles,
                     "run_id": new_run_id, "event_ids": event_ids},
        )
        return {**out, "run_id": new_run_id,
                "approved_count": len(identity_ids), "event_ids": event_ids}


class RedraftOutreachBody(BaseModel):
    """Body for ``POST /campaigns/{cid}/identities/{iid}/redraft-outreach``.

    ``discard_existing_approved_draft`` is the explicit operator ack that
    they understand a re-draft will orphan whatever Gmail draft is
    sitting in their inbox from the prior approval. Without it, the
    endpoint refuses if a prior draft was approved-but-not-sent; this
    keeps a misclick from silently doubling drafts in Gmail.
    """

    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    discard_existing_approved_draft: bool = False


def _compose_redraft_brief(
    *,
    campaign_id: str,
    env: str,
    identity_id: int,
    handle: str | None,
    actor_email: str,
    test_mode_to: str | None,
) -> str:
    lines = [
        "# campaign_redraft_outreach",
        f"campaign_id: {campaign_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
        f"test_mode_to: {test_mode_to or ''}",
        "",
        "# scope",
        f"- identity_id: {identity_id}",
    ]
    if handle:
        lines.append(f"  handle: {handle}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Regenerate the initial outreach draft for the single identity "
            "above. Do NOT process any other identity in this campaign. "
            "Treat this as a re-trigger of the post-approval flow for one "
            "KOL — same skill, same fact-write contract, just scoped to "
            "one identity_id."
        ),
        "",
        "## Pipeline",
        (
            "1. Read campaign_config + dispatch-context for this single "
            "identity_id only. If the identity is not in this campaign's "
            "selected pool, stop and report the mismatch."
        ),
        (
            "2. If `primary_email` is missing, run `kol-email-discovery` "
            "first; on miss, open a `contact_email_not_found` escalation "
            "and stop. Never invent an email."
        ),
        (
            "3. Determine outreach path from CAL (relationship.total_collabs "
            "/ identity.outreach_path). Invoke `kol-cold-outreach` (cold) "
            "or `kol-reengagement-outreach` (repeat) for this identity."
        ),
        (
            "4. Persist results to CAL: emit `kol_initial_outreach_draft_ready` "
            "event AND write the `approval.reply_draft` fact with "
            "`decision=\"pending\", kind=\"initial_outreach\"`. If a "
            "prior approval.reply_draft exists for this (identity, "
            "campaign), overwrite it — the operator explicitly asked "
            "for a fresh draft."
        ),
        (
            "5. Do NOT send email. Do NOT write `offer.outreach_sent=true`. "
            "The console operator approves the new draft separately."
        ),
        "",
        "## Runtime contract",
        f"- Use {_REPO_ROOT}/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py.",
        "- Every CLI call MUST pass --env matching `mode` above.",
        "- In TEST mode, route any draft target to test_mode_to above.",
    ])
    return "\n".join(lines)


@router.post("/{campaign_id}/identities/{identity_id}/redraft-outreach")
async def redraft_outreach(
    campaign_id: str,
    identity_id: int,
    body: RedraftOutreachBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Regenerate the initial-outreach Gmail draft for a single KOL.

    Used from KolDetailPage when the operator sees a KOL stuck in
    "approved but no Gmail draft" or "Gmail draft pending send" and
    wants to (re-)build the draft. Internally launches a focused agent
    run that re-invokes kol-cold-outreach / kol-reengagement-outreach
    for one identity, mirroring approve-shortlist but at single-KOL
    scope.

    Concurrency:
    * 409 ``campaign_run_in_flight`` — a campaign-scoped run is active
      (approve, rediscover, gate retry). Blocking here mirrors the
      approve-shortlist contract so two concurrent drafts can't race on
      the same fact row.
    * 409 ``redraft_inflight`` — another redraft for this exact
      (identity, env, campaign) fired within ``INFLIGHT_TTL_SECONDS``.
      Double-click defense across page reloads (the in-flight key is
      durable in product_campaign_runs, not just local React state).
    * 409 ``approved_draft_exists`` — the previous draft was already
      approved by the operator (a Gmail draft exists in their inbox).
      Re-drafting would orphan that Gmail draft, so we require the
      caller to pass ``discard_existing_approved_draft: true``.
    * 409 ``already_sent`` — ``offer.outreach_sent`` is true; the email
      has been delivered, redrafting is meaningless. Use a follow-up
      flow instead.
    """
    env = body.env
    lock = await campaign_lock(env, campaign_id)
    async with lock:
        in_flight, current_run_id, current_state = await _campaign_run_in_flight(
            conn, gateway, campaign_id=campaign_id, env=env,
        )
        if in_flight:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "campaign_run_in_flight",
                    "message": (
                        "another agent run is still active for this "
                        "campaign — wait for it to finish before "
                        "re-drafting"
                    ),
                    "run_id": current_run_id,
                    "run_state": current_state,
                },
            )

        dedup_key = f"redraft:{env}:{campaign_id}:{identity_id}"
        inflight_self = get_inflight_run(conn, dedup_key=dedup_key)
        if inflight_self is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "redraft_inflight",
                    "message": (
                        f"a redraft for this KOL was triggered in the "
                        f"last {INFLIGHT_TTL_SECONDS}s — wait for the "
                        f"new draft to appear (typically 30–60s) before "
                        f"re-triggering"
                    ),
                    "run_id": inflight_self.get("run_id"),
                    "started_at": inflight_self.get("started_at"),
                },
            )

        # Pre-flight gate. The redraft uses a fresh session_id
        # (``kol-campaign-draft:...``) so the agent does NOT inherit
        # the launch brief's product context; it relies entirely on
        # CAL's campaign_config for product_display_name. Validate
        # upfront so the operator gets a clear 400 instead of waiting
        # 30-60s for the agent to open a
        # campaign_config_missing_required_product_facts escalation.
        await assert_campaign_config_complete(bridge, campaign_id)

        try:
            ident = await bridge.get_identity(identity_id)
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if not ident:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

        try:
            facts_resp = await bridge.read_facts(
                identity_id, campaign_id=campaign_id, env=env,
            )
        except BridgeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        facts = facts_resp.get("facts") if isinstance(facts_resp, dict) else {}
        facts = facts if isinstance(facts, dict) else {}

        if facts.get("offer.outreach_sent"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "already_sent",
                    "message": (
                        "outreach has already been sent for this KOL "
                        "(offer.outreach_sent=true). Use a follow-up "
                        "flow instead of redrafting the initial email."
                    ),
                    "outreach_sent_at": facts.get("offer.outreach_sent_at"),
                },
            )

        prior_reply_draft = facts.get("approval.reply_draft")
        prior_decision = None
        if isinstance(prior_reply_draft, dict):
            prior_decision = prior_reply_draft.get("decision")
        if prior_decision == "approved" and not body.discard_existing_approved_draft:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "approved_draft_exists",
                    "message": (
                        "a previously approved Gmail draft exists for "
                        "this KOL and has not been sent. Re-drafting "
                        "will orphan that Gmail draft in your inbox. "
                        "Confirm by re-submitting with "
                        "discard_existing_approved_draft=true."
                    ),
                    "gmail_draft_id": facts.get("offer.gmail_draft_id"),
                    "gmail_thread_id": facts.get("offer.gmail_thread_id"),
                },
            )

        campaign_row = conn.execute(
            "SELECT sku, test_mode_to FROM product_campaigns "
            "WHERE campaign_id=? AND env=?",
            (campaign_id, env),
        ).fetchone()
        test_mode_to = campaign_row["test_mode_to"] if campaign_row else None
        if env == "TEST" and not test_mode_to:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "test_mode_to missing for TEST campaign; restart the "
                "campaign or set test_mode_to before re-drafting",
            )

        ensure_gateway_bridge_key()
        handle = ident.get("primary_handle") if isinstance(ident, dict) else None
        brief = _compose_redraft_brief(
            campaign_id=campaign_id,
            env=env,
            identity_id=identity_id,
            handle=handle if isinstance(handle, str) else None,
            actor_email=user["email"],
            test_mode_to=test_mode_to,
        )
        try:
            run = await gateway.start_run(
                input=brief,
                instructions=_APPROVAL_INSTRUCTIONS,
                # Share the draft session namespace with refine/preview
                # so transcript replay treats this as a draft run, not a
                # campaign-wide resume that would clutter the campaign
                # transcript view.
                session_id=f"kol-campaign-draft:{env}:{campaign_id}",
            )
        except GatewayError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        new_run_id = run.get("run_id") if isinstance(run, dict) else None
        if isinstance(new_run_id, str) and new_run_id:
            register_run(
                conn,
                campaign_id=campaign_id,
                env=env,
                run_id=new_run_id,
                kind="draft",
                session_id=f"kol-campaign-draft:{env}:{campaign_id}",
                dedup_key=dedup_key,
            )
        write_audit(
            conn,
            actor_user_id=user["id"],
            action="campaign.redraft_outreach",
            target=campaign_id,
            payload={
                "identity_id": identity_id,
                "env": env,
                "run_id": new_run_id,
                "prior_decision": prior_decision,
                "discard_existing_approved_draft": body.discard_existing_approved_draft,
            },
        )
        return {
            "run_id": new_run_id,
            "identity_id": identity_id,
            "campaign_id": campaign_id,
            "env": env,
            "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }


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

    product_display_name: str | None = Field(default=None, min_length=2, max_length=80)
    deliverable_platforms: list[str] | None = None
    deliverable_count_per_platform: int | None = Field(default=None, ge=1, le=20)
    audit_standards_md: str | None = Field(default=None, max_length=8_000)
    color_variant_policy: str | None = Field(default=None, max_length=2_000)
    extra_notes: str | None = Field(default=None, max_length=8_000)
    paid_ceiling: float | None = Field(default=None, gt=0)
    contract_required: bool | None = None
    env: str = Field(default="TEST", pattern="^(LIVE|TEST)$")

    @model_validator(mode="after")
    def _validate_display_name(self) -> "PatchCampaignConfigBody":
        if self.product_display_name is not None:
            self.product_display_name = _validate_product_display_name(
                self.product_display_name,
            )
        return self


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


@router.get("")
async def list_campaigns(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(default=None, pattern="^(LIVE|TEST)$"),
) -> dict:
    """Campaign picker feed: distinct campaigns the bridge has candidates
    for, sorted newest-first. Returns ``{items: [{campaign_id, env,
    candidate_count, last_touched_at, label, status}, ...]}``."""
    try:
        raw = await bridge.list_campaigns(env=env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"items": raw.get("items", [])}


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
            "outreach_sent_at": it.get("outreach_sent_at"),
            "interest_signal": it.get("interest_signal"),
            "outreach_draft_created": bool(it.get("outreach_draft_created")),
            "gmail_draft_id": it.get("gmail_draft_id"),
            "gmail_thread_id": it.get("gmail_thread_id"),
            # Card-level unread inputs (Phase D fix-2). FE compares
            # *_latest_at against a localStorage last-seen timestamp to
            # decide whether to render a red dot.
            "pending_approval_count": int(it.get("pending_approval_count") or 0),
            "pending_approval_latest_at": it.get("pending_approval_latest_at"),
            "open_escalation_count": int(it.get("open_escalation_count") or 0),
            "open_escalation_latest_at": it.get("open_escalation_latest_at"),
            "reply_draft_state": it.get("reply_draft_state"),
        })
    counts_in = raw.get("counts") or {}
    return {
        "campaign_id": campaign_id,
        "lanes": items_out,
        "counts": {
            "pending_approvals": int(counts_in.get("pending_approvals") or 0),
            "open_escalations": int(counts_in.get("open_escalations") or 0),
            "pending_approvals_latest_at": counts_in.get("pending_approvals_latest_at"),
            "open_escalations_latest_at": counts_in.get("open_escalations_latest_at"),
        },
    }
