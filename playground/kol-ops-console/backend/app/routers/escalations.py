"""Proxy routes for escalations list / open / resolve (Phase C-i)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..run_registry import register_run

router = APIRouter(prefix="/escalations", tags=["escalations"])

_RESUME_INSTRUCTIONS = (
    "You are resuming a KOL outreach campaign after a web-console escalation "
    "was answered by the operator.\n"
    "Repo root for file tools is /home/pc/agent_prj/hermes-agent. "
    "For search_files/read_file/write_file/patch, use repo-relative paths "
    "like `plugins/kol-ops-bridge` or absolute paths under "
    "`/home/pc/agent_prj/hermes-agent/`; do NOT prefix file-tool paths with "
    "`./agent_prj/hermes-agent/`. For terminal/Python execution, use "
    "absolute script paths. "
    "Read the campaign, candidate, identity, goal and event state from CAL via "
    "the deterministic kol_bridge_tool.py CLI, always passing the env from the "
    "brief. Do not rerun unrelated discovery. Continue the blocked next step "
    "using the operator answer and facts below. In TEST mode, route any draft "
    "or Gmail test target to campaign_config.test_mode_to. Never send email "
    "without a separate explicit operator approval. Persist progress, draft "
    "records, approvals, or any new escalation through the bridge CLI."
)

_DRAFT_PREVIEW_INSTRUCTIONS = (
    "You are generating a PREVIEW email draft for an open KOL escalation. "
    "Hard rules:\n"
    "- Repo root for file tools is /home/pc/agent_prj/hermes-agent.\n"
    "- For search_files/read_file/write_file/patch, use repo-relative\n"
    "  paths like `plugins/kol-ops-bridge` or absolute paths under\n"
    "  `/home/pc/agent_prj/hermes-agent/`.\n"
    "- Do NOT prefix file-tool paths with `./agent_prj/hermes-agent/`.\n"
    "- For terminal/Python execution, use absolute script paths.\n"
    "- Do NOT call resolve-escalation, write-event, or any state-changing "
    "  bridge endpoint on the escalation row. The operator has NOT yet "
    "  approved a resume; this run only previews what the agent would "
    "  write.\n"
    "- Read campaign_config + escalation + facts via the bridge CLI in "
    "  read-only mode (get-*, list-*).\n"
    "- Pick the appropriate drafting skill for the goal "
    "  (kol-compensation-negotiator for compensation_negotiation, "
    "  kol-contract-coordinator for contract_signing, "
    "  kol-deliverables-clarifier for deliverables_scope, etc.) and "
    "  invoke its draft branch with operator_answer + operator_facts.\n"
    "- Write the resulting draft as a single ``approval.reply_draft`` "
    "  fact via ``kol_bridge_tool.py write-facts --namespace approval "
    "  --json @/tmp/draft.json``. The JSON body MUST set "
    "  ``campaign_id`` to the campaign_id from the brief above and "
    "  include ``linked_escalation_id`` in the fact value so the "
    "  console can correlate this preview with the escalation.\n"
    "- In TEST mode, route any draft target to campaign_config.test_mode_to.\n"
    "- Never send email. Do not create Gmail drafts here — the operator "
    "  approves the preview separately on the Approvals page, which is "
    "  what triggers the actual Gmail draft creation."
)


def _compose_draft_preview_brief(
    *,
    escalation: dict[str, Any],
    operator_answer: str,
    operator_facts: dict[str, Any],
    actor_email: str,
) -> str:
    return "\n".join([
        "# escalation_draft_preview",
        f"escalation_id: {escalation.get('id')}",
        f"campaign_id: {escalation.get('campaign_id') or ''}",
        f"identity_id: {escalation.get('identity_id') or ''}",
        f"mode: {escalation.get('env') or 'LIVE'}",
        f"goal: {escalation.get('goal') or ''}",
        f"reason: {escalation.get('reason') or ''}",
        f"requested_by: {actor_email}",
        "",
        "# operator_answer",
        operator_answer.strip(),
        "",
        "# operator_facts_json",
        json.dumps(operator_facts, ensure_ascii=False, sort_keys=True),
        "",
        "# resume_context_json",
        json.dumps(escalation.get("resume_context") or {}, ensure_ascii=False, sort_keys=True),
        "",
        "# required_output",
        ("Write exactly one approval.reply_draft fact via the bridge CLI. "
         "Set linked_escalation_id to the escalation_id above and set "
         "campaign_id in the JSON body to the campaign_id above (required "
         "so the approval inherits campaign scope). Do NOT resolve the "
         "escalation or send mail. After writing the fact, report the "
         "fact_path back so the console can poll for it."),
    ])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


class OpenEscalationBody(BaseModel):
    identity_id: int
    campaign_id: str
    rule_id: Optional[str] = None
    reason: str = Field(min_length=1, max_length=2000)
    suggested_question: Optional[str] = None
    parent_id: Optional[int] = None
    env: Optional[str] = None


class ResolveEscalationBody(BaseModel):
    decision: str = Field(pattern="^(resume|terminate)$")
    operator_answer: str = Field(min_length=0, max_length=4000, default="")
    operator_facts: dict[str, Any] = Field(default_factory=dict)
    final_state: Optional[str] = None
    env: Optional[str] = Field(default=None, pattern="^(LIVE|TEST)$")


class DraftPreviewBody(BaseModel):
    """Body for POST /escalations/{id}/preview-draft.

    Same shape as the resolve body so the operator can draft *with* the
    answer + facts they're about to submit, without committing yet.
    The agent never resolves the escalation during this run.
    """
    operator_answer: str = Field(min_length=0, max_length=4000, default="")
    operator_facts: dict[str, Any] = Field(default_factory=dict)
    env: Optional[str] = Field(default=None, pattern="^(LIVE|TEST)$")


def _normalize_escalation_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map bridge column names to the frontend's ``EscalationRow``.

    Bridge returns ``parent_escalation_id`` (raw column) but the
    EscalationConsolePage references ``parent_id``. Also surface
    ``rule_id`` / ``suggested_question`` from ``resume_context`` so the
    operator console doesn't need to dig into the JSON blob.
    """
    out = dict(raw)
    if "parent_id" not in out:
        out["parent_id"] = out.get("parent_escalation_id")
    ctx = out.get("resume_context") or {}
    if isinstance(ctx, dict):
        out.setdefault("rule_id", ctx.get("matched_rule_id") or ctx.get("rule_id"))
    if not out.get("suggested_question"):
        out["suggested_question"] = out.get("question_to_operator")
    if not out.get("suggested_question") and isinstance(ctx, dict):
        out["suggested_question"] = ctx.get("suggested_question")
    return out


async def _find_escalation(
    bridge: BridgeClient,
    escalation_id: int,
    preferred_env: str | None,
) -> dict[str, Any] | None:
    envs = []
    if preferred_env:
        envs.append(preferred_env.upper())
    envs.extend(["TEST", "LIVE"])
    seen: set[str] = set()
    for env in envs:
        if env in seen:
            continue
        seen.add(env)
        for row in await bridge.list_escalations(env=env):
            if row.get("id") == escalation_id:
                return _normalize_escalation_row(row)
    return None


def _compose_resume_brief(
    *,
    escalation: dict[str, Any],
    operator_answer: str,
    operator_facts: dict[str, Any],
    actor_email: str,
    require_draft: bool = False,
) -> str:
    next_step_lines = [
        "Continue the blocked campaign step for this escalation. If the "
        "answer does not provide the facts needed to proceed safely, open "
        "a new specific escalation instead of inventing data.",
    ]
    if require_draft:
        next_step_lines.append(
            "Because this escalation was opened by the reply dispatcher "
            "for an inbound KOL message and no preview draft exists yet, "
            "you MUST also produce a reply draft for the operator to "
            "review: invoke the appropriate drafting skill for the active "
            "goal (kol-deliverables-clarifier for deliverables_scope, "
            "kol-compensation-negotiator for compensation_negotiation, "
            "kol-contract-coordinator for contract_signing, etc.) and "
            "write exactly one approval.reply_draft fact via "
            "`kol_bridge_tool.py write-facts --namespace approval --json "
            "@/tmp/draft.json`. The JSON body MUST set campaign_id to the "
            "campaign_id above and the fact value MUST include "
            "linked_escalation_id pointing at the escalation_id above. "
            "Do not call resolve-escalation; the escalation has already "
            "been resolved by the console."
        )
    return "\n".join([
        "# escalation_resume",
        f"escalation_id: {escalation.get('id')}",
        f"campaign_id: {escalation.get('campaign_id') or ''}",
        f"identity_id: {escalation.get('identity_id') or ''}",
        f"mode: {escalation.get('env') or 'LIVE'}",
        f"goal: {escalation.get('goal') or ''}",
        f"reason: {escalation.get('reason') or ''}",
        f"resumed_by: {actor_email}",
        "",
        "# operator_answer",
        operator_answer.strip(),
        "",
        "# operator_facts_json",
        json.dumps(operator_facts, ensure_ascii=False, sort_keys=True),
        "",
        "# resume_context_json",
        json.dumps(escalation.get("resume_context") or {}, ensure_ascii=False, sort_keys=True),
        "",
        "# required_next_step",
        " ".join(next_step_lines),
    ])


def _escalation_needs_reply_draft(escalation: dict[str, Any]) -> bool:
    """True iff this escalation was opened because an inbound KOL reply
    is waiting for us — i.e. the reply dispatcher created it. For those
    cases, the campaign cannot make progress without us sending a reply.

    Internal escalations (e.g. compensation_cap_breach raised by a skill
    while drafting outbound) don't need a fresh reply on resume.
    """
    ctx = escalation.get("resume_context") or {}
    if not isinstance(ctx, dict):
        return False
    return ctx.get("source") == "dispatcher" and bool(ctx.get("source_message_id"))


async def _has_pending_reply_draft(
    bridge: BridgeClient, escalation_id: int, env: str
) -> bool:
    """Check whether a pending ``approval.reply_draft`` already exists
    that is linked to this escalation (typically written by a prior
    ``preview-draft`` run). Used so resolve doesn't write a duplicate.
    """
    try:
        rows = await bridge.list_approvals(status="pending", env=env)
    except BridgeError:
        # Fail-open: assume no existing draft so resume still drafts one.
        # The bridge-side dedup (write-facts inheriting campaign_id) is a
        # separate concern; for draft duplication, prefer producing a
        # draft over silently dropping one.
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("fact_key") != "approval.reply_draft":
            continue
        value = row.get("value")
        if not isinstance(value, dict):
            continue
        if value.get("linked_escalation_id") == escalation_id:
            return True
    return False


@router.get("")
async def list_escalations(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    state: Optional[str] = Query(None),
    env: Optional[str] = Query(None),
) -> list[dict]:
    try:
        rows = await bridge.list_escalations(state=state, env=_env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return [_normalize_escalation_row(r) for r in rows if isinstance(r, dict)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def open_escalation(
    body: OpenEscalationBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["env"] = _env(payload.get("env"))
    try:
        out = await bridge.open_escalation(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.open",
        target=str(body.identity_id),
        payload={"rule_id": body.rule_id, "campaign_id": body.campaign_id},
    )
    return out


@router.patch("/{escalation_id}")
async def resolve_escalation(
    escalation_id: int,
    body: ResolveEscalationBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    try:
        escalation = await _find_escalation(bridge, escalation_id, body.env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    payload = body.model_dump(exclude_none=True)
    payload["decided_by"] = f"web:{user['email']}"
    try:
        out = await bridge.resolve_escalation(escalation_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id: str | None = None
    if body.decision == "resume" and escalation and escalation.get("campaign_id"):
        env = str(escalation.get("env") or body.env or _env(None)).upper()
        campaign_id = str(escalation["campaign_id"])
        require_draft = False
        if _escalation_needs_reply_draft(escalation):
            already_has_draft = await _has_pending_reply_draft(
                bridge, escalation_id, env,
            )
            require_draft = not already_has_draft
        brief = _compose_resume_brief(
            escalation=escalation,
            operator_answer=body.operator_answer,
            operator_facts=body.operator_facts,
            actor_email=user["email"],
            require_draft=require_draft,
        )
        try:
            run = await gateway.start_run(
                input=brief,
                instructions=_RESUME_INSTRUCTIONS,
                session_id=f"kol-campaign:{env}:{campaign_id}",
            )
        except GatewayError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if isinstance(run.get("run_id"), str):
            run_id = run["run_id"]
            conn.execute(
                "UPDATE product_campaigns SET run_id=?, status='running' "
                "WHERE campaign_id=? AND env=?",
                (run_id, campaign_id, env),
            )
            register_run(
                conn,
                campaign_id=campaign_id,
                env=env,
                run_id=run_id,
                kind="resume",
                session_id=f"kol-campaign:{env}:{campaign_id}",
            )
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.resolve",
        target=str(escalation_id),
        payload={"decision": body.decision, "run_id": run_id},
    )
    return {**out, "run_id": run_id}


@router.post("/{escalation_id}/preview-draft")
async def preview_draft(
    escalation_id: int,
    body: DraftPreviewBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
    conn=Depends(get_conn),
) -> dict:
    """Trigger a *draft-only* gateway run for an open escalation.

    The agent reads the escalation + operator answer + facts and writes
    an ``approval.reply_draft`` fact via the bridge. It must NOT
    transition the escalation state. The operator reviews the draft on
    the Approvals page; clicking 批准 there is what creates the actual
    Gmail draft (existing approval flow).
    """
    try:
        escalation = await _find_escalation(bridge, escalation_id, body.env)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not escalation:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "escalation not found")
    if not escalation.get("campaign_id"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "preview-draft requires a campaign-scoped escalation",
        )
    env = str(escalation.get("env") or body.env or _env(None)).upper()
    campaign_id = str(escalation["campaign_id"])
    brief = _compose_draft_preview_brief(
        escalation=escalation,
        operator_answer=body.operator_answer,
        operator_facts=body.operator_facts,
        actor_email=user["email"],
    )
    try:
        run = await gateway.start_run(
            input=brief,
            instructions=_DRAFT_PREVIEW_INSTRUCTIONS,
            # Distinct session-id namespace so the preview run is not
            # mistakenly treated as a resume by replay logic.
            session_id=f"kol-campaign-draft:{env}:{campaign_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-campaign-draft:{env}:{campaign_id}",
        )
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.preview_draft",
        target=str(escalation_id),
        payload={"run_id": run_id, "campaign_id": campaign_id},
    )
    return {"ok": True, "run_id": run_id,
            "hint": "Watch the Approvals page for an approval.reply_draft "
                    "fact linked to this escalation. The agent writes it "
                    "asynchronously; refresh in 30–60 s."}
