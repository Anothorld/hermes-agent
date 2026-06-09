"""Proxy routes for escalations list / open / resolve (Phase C-i)."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

_REPO_ROOT = str(Path(__file__).resolve().parents[5])

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..bridge_runtime import ensure_gateway_bridge_key
from ..campaign_config_sync import assert_campaign_config_complete
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..bridge_agent_contract_loader import (
    draft_preview_cli_checklist,
    gateway_contract_block,
    resume_cli_checklist,
    terminal_safety_rules,
)
from ..run_registry import get_inflight_run, register_run


def _preview_draft_dedup_key(escalation_id: int) -> str:
    """Stable key for deduping preview-draft + resume-draft runs for one
    escalation. The escalation-level resume run reuses this same key when
    it would also draft (require_draft=True) so a preview followed
    immediately by a resume cannot produce two parallel drafts."""
    return f"draft:escalation:{escalation_id}"

router = APIRouter(prefix="/escalations", tags=["escalations"])

_BRIDGE_AGENT_HARD_RULES = gateway_contract_block() + "\n"

_RESUME_INSTRUCTIONS = (
    "You are resuming a KOL outreach campaign after a web-console escalation "
    "was answered by the operator.\n"
    f"{_BRIDGE_AGENT_HARD_RULES}"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"Repo root for file tools is {_REPO_ROOT}. "
    "Do NOT read or search under `plugins/kol-ops-bridge/` for API discovery. "
    "For bridge I/O use the native **terminal** tool with one "
    "`kol_bridge_tool.py` subcommand per call (not execute_code). "
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
    f"{_BRIDGE_AGENT_HARD_RULES}"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- Do NOT read or search `plugins/kol-ops-bridge/` for API discovery.\n"
    "- Use the **terminal** tool with `kol_bridge_tool.py` (not execute_code/curl).\n"
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
    "- Before drafting: `kol-email-style-loader` (pass `--owner-user-id` from "
    "  brief `requested_by_user_id` when present) + `kol-creator-brief-loader`, "
    "  then `humanizer`. Follow that skill's body format contract (HTML for "
    "  outreach-style drafts; reply-thread drafts per skill).\n"
    "- Persist the draft via ``kol_bridge_tool.py persist-reply-draft``. "
    "The JSON body MUST set ``campaign_id``, ``source_message_id`` "
    "from resume_context, and ``linked_escalation_id`` in the fact "
    "value so the console can correlate this preview with the "
    "escalation.\n"
    "- Preserve dollar amounts exactly. Never place JSON containing `$` "
    "  amounts in an unquoted heredoc or inline double-quoted shell "
    "  string; bash expands `$3000` to `000` and `$800` to `00`. Use "
    "  `cat <<'JSON' > /tmp/draft.json` or Python `json.dump`, then pass "
    "  `--json @/tmp/draft.json`. Re-read the fact and reject outputs "
    "  containing `000 quote` or `00 total`.\n"
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
    actor_user_id: int | None = None,
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
        f"requested_by_user_id: {actor_user_id if actor_user_id is not None else ''}",
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
        "",
        draft_preview_cli_checklist(
            escalation_id=escalation.get("id") or 0,
            identity_id=escalation.get("identity_id") or 0,
            campaign_id=str(escalation.get("campaign_id") or ""),
            env=str(escalation.get("env") or "LIVE"),
            operator_user_id=actor_user_id,
        ),
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
    reason_tags: list[str] = Field(default_factory=list, max_length=5)
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
        pending = ctx.get("pending_inbounds")
        if isinstance(pending, list):
            out["pending_inbound_count"] = len(pending)
            latest = ctx.get("latest_pending_inbound_message_id")
            if isinstance(latest, str) and latest.strip():
                out["latest_pending_inbound_message_id"] = latest.strip()
    if not out.get("suggested_question"):
        out["suggested_question"] = out.get("question_to_operator")
    if not out.get("suggested_question") and isinstance(ctx, dict):
        out["suggested_question"] = ctx.get("suggested_question")
    return out


def _parse_iso_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _escalation_priority(row: dict[str, Any], env: str) -> tuple[int, int]:
    score = 100
    reason = str(row.get("reason") or "")
    created_at = _parse_iso_ts(row.get("created_at"))
    now = _dt.datetime.now(_dt.timezone.utc)

    if env.upper() == "LIVE":
        score -= 30
    if row.get("state") == "awaiting_answer":
        score -= 20
    if reason.startswith("discovery_floor_unmet") or reason.startswith("campaign_config_incomplete"):
        score -= 10
    if created_at is not None:
        age_sec = (now - created_at).total_seconds()
        if age_sec >= 2 * 3600:
            score -= 20
        elif age_sec >= 30 * 60:
            score -= 10
    created_ts = int(created_at.timestamp()) if created_at is not None else 0
    return score, -created_ts


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
    actor_user_id: int | None = None,
    require_draft: bool = False,
) -> str:
    next_step_lines = [
        "Continue the blocked campaign step for this escalation. If the "
        "answer does not provide the facts needed to proceed safely, open "
        "a new specific escalation instead of inventing data.",
    ]
    if require_draft:
        next_step_lines.append(
            "This escalation was opened for an inbound KOL message and "
            "no linked preview draft exists yet — you MUST produce a "
            "formal reply draft reflecting the operator_answer and "
            "operator_facts above AND every entry in "
            "resume_context.pending_inbounds (trigger + follow-ups); "
            "use latest_pending_inbound_message_id as the Gmail reply "
            "anchor when present. Do not ignore follow-up questions "
            "that arrived while the escalation was open (not a stall "
            "note like \"we're reviewing\" or \"will get back shortly\"). "
            "BEFORE "
            "drafting, re-check `kol_bridge_tool.py list-approvals "
            "--status pending --env <env>` for a row where "
            "`value.linked_escalation_id` equals the escalation_id "
            "above; if present, skip drafting. Supersede any stale "
            "pending draft that only has chase_supersede and no "
            "linked_escalation_id. Invoke the drafting skill for the "
            "active goal (kol-compensation-negotiator for "
            "compensation_negotiation, etc.). Before drafting: "
            "`kol-email-style-loader` (pass `--owner-user-id` from brief "
            "`requested_by_user_id` when present) + "
            "`kol-creator-brief-loader`, then `humanizer`. Persist via "
            "`kol_bridge_tool.py persist-reply-draft` with "
            "source_message_id = resume_context.latest_pending_inbound_message_id "
            "when set, else resume_context.source_message_id, and fact value "
            "including linked_escalation_id=<escalation_id>. Do not "
            "call resolve-escalation; the console already resolved "
            "this row."
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
        f"requested_by_user_id: {actor_user_id if actor_user_id is not None else ''}",
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
        "",
        resume_cli_checklist(
            escalation_id=escalation.get("id") or 0,
            identity_id=escalation.get("identity_id") or 0,
            campaign_id=str(escalation.get("campaign_id") or ""),
            env=str(escalation.get("env") or "LIVE"),
            require_draft=require_draft,
            operator_user_id=actor_user_id,
        ),
    ])


def _escalation_inbound_message_id(
    escalation: dict[str, Any],
    *,
    inferred_inbound_message_id: str | None = None,
) -> str | None:
    """Best inbound anchor for reply drafting on resume."""
    ctx = escalation.get("resume_context") or {}
    if isinstance(ctx, dict):
        raw = ctx.get("source_message_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if isinstance(inferred_inbound_message_id, str) and inferred_inbound_message_id.strip():
        return inferred_inbound_message_id.strip()
    return None


def _is_inbound_escalation_source(ctx: dict[str, Any]) -> bool:
    """True when resume_context tags an inbound-reply escalation."""
    source = ctx.get("source")
    if source in ("classifier", "dispatcher"):
        return True
    # Legacy rows: explicit inbound anchor without a source tag.
    if source is None and ctx.get("source_message_id"):
        return True
    return False


def _escalation_needs_reply_draft(
    escalation: dict[str, Any],
    *,
    inferred_inbound_message_id: str | None = None,
) -> bool:
    """True when an inbound KOL reply is waiting for a post-resume draft.

    Covers classifier- and dispatcher-tagged escalations plus legacy rows
    that carry ``source_message_id``. Internal-only escalations (other
    ``source`` values, or ``source`` missing without an explicit anchor)
    are excluded even when the timeline has unrelated inbound history.
    """
    if not escalation.get("campaign_id") or not escalation.get("identity_id"):
        return False
    ctx = escalation.get("resume_context") or {}
    if not isinstance(ctx, dict) or not _is_inbound_escalation_source(ctx):
        return False
    msg_id = _escalation_inbound_message_id(
        escalation,
        inferred_inbound_message_id=inferred_inbound_message_id,
    )
    return bool(msg_id)


def _resume_draft_followup(
    *,
    needs_draft: bool,
    require_draft: bool,
    already_has_draft: bool,
    draft_in_flight: bool,
) -> tuple[bool, str]:
    """Map resume draft state to ``draft_expected`` + ``draft_followup``."""
    if not needs_draft:
        return False, "none"
    if already_has_draft:
        return False, "already_pending"
    if draft_in_flight:
        return False, "in_flight"
    if require_draft:
        return True, "expected"
    return False, "none"


async def _infer_inbound_message_id_for_escalation(
    bridge: BridgeClient,
    escalation: dict[str, Any],
    env: str,
) -> str | None:
    """Timeline fallback when resume_context omitted source_message_id."""
    identity_id = escalation.get("identity_id")
    campaign_id = escalation.get("campaign_id")
    if not isinstance(identity_id, int) or not isinstance(campaign_id, str):
        return None
    try:
        events = await bridge.get_timeline(
            identity_id,
            env=env,
            campaign_id=campaign_id,
            limit=200,
        )
    except BridgeError:
        return None
    inbound = _pick_inbound_for_escalation(
        events=events,
        escalation_created_at=escalation.get("created_at"),
    )
    if not inbound:
        return None
    msg = inbound.get("message_id")
    return msg if isinstance(msg, str) and msg.strip() else None


async def _has_pending_reply_draft(
    bridge: BridgeClient, escalation_id: int, env: str
) -> bool:
    """Check whether a pending ``approval.reply_draft`` already exists
    that is linked to this escalation (typically written by a prior
    ``preview-draft`` run). Used so resolve doesn't write a duplicate.

    Pending rows that only carry ``chase_supersede`` (no
    ``linked_escalation_id``) intentionally do **not** block resume
    drafting — those are stale chase placeholders to supersede.
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
    identity_id: Optional[int] = Query(None, ge=1),
    campaign_id: Optional[str] = Query(None),
) -> list[dict]:
    resolved_env = _env(env)
    try:
        rows = await bridge.list_escalations(
            state=state,
            env=resolved_env,
            identity_id=identity_id,
            campaign_id=campaign_id,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    normalized = [_normalize_escalation_row(r) for r in rows if isinstance(r, dict)]
    normalized.sort(key=lambda r: _escalation_priority(r, resolved_env))
    return normalized


def _pick_inbound_for_escalation(
    *,
    events: list[dict[str, Any]],
    escalation_created_at: str | None,
) -> dict[str, Any] | None:
    """Find the kol_inbound_reply event most likely to have triggered the
    escalation.

    Strategy: prefer the most recent inbound whose ts is ≤ the
    escalation's created_at (the inbound that caused the dispatcher to
    open the escalation). Fall back to the most recent inbound on the
    timeline if no created_at is available or none precedes it.
    Returns a normalized dict ``{from_addr, subject, body, snippet, date,
    message_id, thread_id, ts}`` or None.
    """
    inbounds = [
        ev for ev in events
        if isinstance(ev, dict) and ev.get("event_type") == "kol_inbound_reply"
    ]
    if not inbounds:
        return None
    # Bridge ``list_events`` returns reverse-chronological (newest first).
    if escalation_created_at:
        for ev in inbounds:
            ev_ts = ev.get("ts") or ""
            if ev_ts and ev_ts <= escalation_created_at:
                return _shape_inbound(ev)
    return _shape_inbound(inbounds[0])


def _shape_inbound(ev: dict[str, Any]) -> dict[str, Any]:
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return {
        "event_id": ev.get("id"),
        "ts": ev.get("ts"),
        "from_addr": payload.get("from_addr"),
        "subject": payload.get("subject"),
        "body": payload.get("body"),
        "snippet": payload.get("snippet"),
        "date": payload.get("date"),
        "message_id": payload.get("message_id"),
        "thread_id": payload.get("thread_id"),
    }


def _inbound_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event_type") != "kol_inbound_reply":
            continue
        shaped = _shape_inbound(ev)
        mid = shaped.get("message_id")
        if isinstance(mid, str) and mid.strip():
            out[mid.strip()] = shaped
    return out


def _shape_inbound_from_anchor(anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": anchor.get("event_id"),
        "ts": anchor.get("ts"),
        "from_addr": anchor.get("from_addr"),
        "subject": anchor.get("subject"),
        "body": None,
        "snippet": anchor.get("snippet"),
        "date": None,
        "message_id": anchor.get("message_id"),
        "thread_id": anchor.get("thread_id"),
    }


def _role_label(role: str, followup_index: int) -> str:
    if role == "trigger":
        return "触发升级"
    if followup_index <= 1:
        return "追信（待处理）"
    return f"追信 #{followup_index}（待处理）"


def _collect_pending_inbounds(
    escalation: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge ``resume_context.pending_inbounds`` with timeline bodies."""
    ctx = escalation.get("resume_context") or {}
    anchors = ctx.get("pending_inbounds") if isinstance(ctx, dict) else None
    by_msg = _inbound_index(events)
    if isinstance(anchors, list) and anchors:
        rows: list[dict[str, Any]] = []
        followup_n = 0
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            mid = anchor.get("message_id")
            if not isinstance(mid, str) or not mid.strip():
                continue
            shaped = by_msg.get(mid.strip()) or _shape_inbound_from_anchor(anchor)
            role = str(anchor.get("role") or "followup")
            if role == "followup":
                followup_n += 1
            rows.append({
                **shaped,
                "role": role,
                "label": _role_label(role, followup_n),
            })
        return rows
    trigger = _pick_inbound_for_escalation(
        events=events,
        escalation_created_at=escalation.get("created_at"),
    )
    if not trigger:
        return []
    created = escalation.get("created_at")
    extras: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event_type") != "kol_inbound_reply":
            continue
        if created and (ev.get("ts") or "") <= created:
            continue
        shaped = _shape_inbound(ev)
        if shaped.get("message_id") == trigger.get("message_id"):
            continue
        extras.append({**shaped, "role": "followup", "label": "追信（待处理）"})
    extras.reverse()
    return [
        {**trigger, "role": "trigger", "label": "触发升级"},
        *extras,
    ]


@router.get("/{escalation_id}/inbound-context")
async def escalation_inbound_context(
    escalation_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: Optional[str] = Query(None),
) -> dict[str, Any]:
    """Return the inbound email that most likely triggered this escalation.

    Looks up the escalation, then the KOL's per-campaign timeline, then
    picks the latest ``kol_inbound_reply`` whose timestamp precedes the
    escalation's ``created_at``. Returns ``{escalation_id, inbound: {...}}``
    or ``{inbound: null}`` when no inbound is on file (e.g. discovery-
    phase escalations like missing campaign_config).
    """
    escalation = await _find_escalation(bridge, escalation_id, env)
    if escalation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "escalation not found")
    e = (env or escalation.get("env") or "TEST").upper()
    identity_id = escalation.get("identity_id")
    campaign_id = escalation.get("campaign_id")
    if not isinstance(identity_id, int):
        return {"escalation_id": escalation_id, "inbound": None}
    if str(escalation.get("state") or "") == "awaiting_answer":
        try:
            await bridge.sync_escalation_pending_inbounds(escalation_id)
            escalation = await _find_escalation(bridge, escalation_id, env) or escalation
        except BridgeError:
            pass
    try:
        events = await bridge.get_timeline(
            identity_id,
            env=e,
            campaign_id=campaign_id if isinstance(campaign_id, str) else None,
            limit=200,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    pending_inbounds = _collect_pending_inbounds(escalation, events)
    inbound = pending_inbounds[0] if pending_inbounds else _pick_inbound_for_escalation(
        events=events,
        escalation_created_at=escalation.get("created_at"),
    )
    ctx = escalation.get("resume_context") or {}
    latest_mid = (
        ctx.get("latest_pending_inbound_message_id")
        if isinstance(ctx, dict)
        else None
    )
    return {
        "escalation_id": escalation_id,
        "identity_id": identity_id,
        "campaign_id": campaign_id,
        "env": e,
        "inbound": inbound,
        "pending_inbounds": pending_inbounds,
        "pending_inbound_count": len(pending_inbounds),
        "latest_pending_inbound_message_id": latest_mid,
    }


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
    will_resume = bool(
        body.decision == "resume" and escalation and escalation.get("campaign_id")
    )
    if will_resume:
        ensure_gateway_bridge_key()
    payload = body.model_dump(exclude_none=True)
    payload["decided_by"] = f"web:{user['email']}"
    try:
        out = await bridge.resolve_escalation(escalation_id, payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id: str | None = None
    draft_expected = False
    draft_followup = "none"
    if body.decision == "resume" and escalation and escalation.get("campaign_id"):
        env = str(escalation.get("env") or body.env or _env(None)).upper()
        campaign_id = str(escalation["campaign_id"])
        require_draft = False
        draft_dedup_key: str | None = None
        needs_draft = False
        already_has_draft = False
        draft_in_flight = False
        inferred_inbound = await _infer_inbound_message_id_for_escalation(
            bridge, escalation, env,
        )
        needs_draft = _escalation_needs_reply_draft(
            escalation,
            inferred_inbound_message_id=inferred_inbound,
        )
        if needs_draft:
            # The has-pending check + the agent re-running this check
            # inside the resume brief together close the race where a
            # preview-draft was triggered in parallel: the console
            # advisory check below catches the common case; the
            # in-brief instruction handles the narrow window between
            # this check and the agent actually starting to draft.
            already_has_draft = await _has_pending_reply_draft(
                bridge, escalation_id, env,
            )
            require_draft = not already_has_draft
            # When this resume would also draft, share the in-flight
            # dedup key with preview_draft so a concurrent preview is
            # refused (and vice versa). This is the only place where
            # resume writes the approval.reply_draft fact.
            if require_draft:
                draft_dedup_key = _preview_draft_dedup_key(escalation_id)
                inflight = get_inflight_run(conn, dedup_key=draft_dedup_key)
                if inflight is not None:
                    # A preview-draft is in flight — let the agent
                    # finish that one instead of racing it. Resume the
                    # campaign WITHOUT drafting; the existing draft run
                    # will surface on the Approvals page on its own.
                    require_draft = False
                    draft_dedup_key = None
                    draft_in_flight = True
            draft_expected, draft_followup = _resume_draft_followup(
                needs_draft=needs_draft,
                require_draft=require_draft,
                already_has_draft=already_has_draft,
                draft_in_flight=draft_in_flight,
            )
        brief = _compose_resume_brief(
            escalation=escalation,
            operator_answer=body.operator_answer,
            operator_facts=body.operator_facts,
            actor_email=user["email"],
            actor_user_id=user.get("id"),
            require_draft=require_draft,
        )
        session_id = f"kol-campaign:{env}:{campaign_id}"
        try:

            async def _start_resume() -> dict[str, Any]:
                return await gateway.start_run(
                    input=brief,
                    instructions=_RESUME_INSTRUCTIONS,
                    session_id=session_id,
                )

            run = await gateway.launch_via_queue(
                _start_resume,
                session_id=session_id,
                dedup_key=draft_dedup_key,
            )
        except GatewayError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if isinstance(run.get("run_id"), str):
            run_id = run["run_id"]
            gateway.ensure_run_drained(run_id)
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
                dedup_key=draft_dedup_key,
            )
    write_audit(
        conn, actor_user_id=user["id"], action="escalation.resolve",
        target=str(escalation_id),
        payload={
            "decision": body.decision,
            "run_id": run_id,
            "reason_tags": body.reason_tags,
        },
    )
    return {
        **out,
        "run_id": run_id,
        "draft_expected": draft_expected,
        "draft_followup": draft_followup,
    }


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
    # Only an open escalation needs a preview draft. Once the operator
    # has resolved (resume/terminate) or the row has been re-escalated,
    # drafting again would write a stale fact onto a closed flow.
    esc_state = str(escalation.get("state") or "").lower()
    if esc_state and esc_state != "awaiting_answer":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"escalation is already {esc_state}; cannot preview-draft",
        )
    env = str(escalation.get("env") or body.env or _env(None)).upper()
    campaign_id = str(escalation["campaign_id"])
    # In-flight dedup: if a draft run for this escalation was started in
    # the last 5 min, refuse and return the existing run_id so the
    # frontend can surface "already generating" instead of spawning a
    # second writer for the same approval.reply_draft fact.
    dedup_key = _preview_draft_dedup_key(escalation_id)
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "error": "draft_already_in_flight",
                "message": (
                    "A draft run for this escalation is already in "
                    "progress. Wait for it to finish (typically 30–60 s) "
                    "or refresh the Approvals page."
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )
    # preview-draft uses a fresh draft-namespace session_id (see below),
    # so the agent does NOT inherit any prior campaign transcript. The
    # child skill that writes approval.reply_draft must therefore read
    # product_display_name from CAL. Fail fast on incomplete config so
    # the operator gets an actionable 400 instead of a fresh
    # campaign_config_missing_required_product_facts escalation.
    await assert_campaign_config_complete(bridge, campaign_id)
    brief = _compose_draft_preview_brief(
        escalation=escalation,
        operator_answer=body.operator_answer,
        operator_facts=body.operator_facts,
        actor_email=user["email"],
        actor_user_id=user.get("id"),
    )
    ensure_gateway_bridge_key()
    session_id = f"kol-campaign-draft:{env}:{campaign_id}"
    try:

        async def _start_preview() -> dict[str, Any]:
            return await gateway.start_run(
                input=brief,
                instructions=_DRAFT_PREVIEW_INSTRUCTIONS,
                session_id=session_id,
            )

        run = await gateway.launch_via_queue(
            _start_preview,
            session_id=session_id,
            dedup_key=dedup_key,
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id:
        gateway.ensure_run_drained(run_id)
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-campaign-draft:{env}:{campaign_id}",
            dedup_key=dedup_key,
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
