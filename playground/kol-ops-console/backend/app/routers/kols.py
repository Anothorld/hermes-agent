"""KOL list + detail (merge bridge identities with local notes)."""

from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from ..audit import write_audit
from ..bridge_client import BridgeClient, BridgeError
from ..bridge_runtime import ensure_gateway_bridge_key
from ..campaign_id_norm import CampaignIdNormaliserMixin
from ..config import get_settings
from ..deps import current_user, get_bridge, get_conn, get_gateway, require_role
from ..gateway_client import GatewayClient, GatewayError
from ..run_registry import (
    INFLIGHT_TTL_SECONDS,
    get_inflight_run,
    register_run,
)

router = APIRouter(prefix="/kols", tags=["kols"])

_REPO_ROOT = str(Path(__file__).resolve().parents[5])


def _env(env: str | None) -> str:
    return (env or get_settings().env).upper()


@router.get("/archive")
async def list_archived_kols(
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    last_outcome: str | None = Query(None, max_length=60),
    platform: str | None = Query(None, max_length=40),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Cross-campaign KOL pool: identities with ≥1 archived collab.

    Powers the past-collab browser at ``/kols/archive`` in the UI.
    Returns ``{total, limit, offset, items, env}``.
    """
    try:
        return await bridge.list_archived_kols(
            env=_env(env), q=q, last_outcome=last_outcome,
            platform=platform, limit=limit, offset=offset,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/{identity_id}")
async def get_kol(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        identity = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    notes = conn.execute(
        "SELECT id, body, author_user_id, created_at FROM kol_notes "
        "WHERE kol_identity_id=? ORDER BY created_at DESC",
        (identity_id,),
    ).fetchall()
    identity["notes"] = [dict(n) for n in notes]
    return identity


@router.get("/{identity_id}/timeline")
async def get_timeline(
    identity_id: int,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    _: Annotated[dict, Depends(current_user)],
    env: str | None = Query(None),
) -> dict:
    try:
        return await bridge.get_timeline(identity_id, _env(env))
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class NoteBody(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{identity_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    identity_id: int,
    body: NoteBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    conn.execute(
        "INSERT INTO kol_notes (kol_identity_id, author_user_id, body, created_at) VALUES (?,?,?,?)",
        (identity_id, user["id"], body.body,
         _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
    )
    write_audit(conn, actor_user_id=user["id"], action="kol.note.add", target=str(identity_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Email enrichment — manual entry + skill-driven discovery
# ---------------------------------------------------------------------------
#
# The detail page surfaces these two paths when a KOL has no
# ``primary_email``. The cold-outreach skill won't draft for an
# identity without a verified email (see
# campaigns.py:_APPROVAL_INSTRUCTIONS step 4), so the kanban "draft
# missing" state is often really an "email missing" state. Giving the
# operator a one-click manual fill + a one-click "ask the web" button
# is the shortest path back to an unblocked draft.


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SetEmailBody(CampaignIdNormaliserMixin):
    """Body for ``POST /kols/{identity_id}/email``.

    ``email`` is the new primary_email value. ``campaign_id`` is
    optional and only used as provenance on the audit + facts row.
    ``override_existing`` is the explicit ack that a previously set
    email will be overwritten — without it, the endpoint refuses if
    primary_email is already non-empty. (Discovery skill writes are
    idempotent in their own check; this guard is for the operator
    manually editing.)
    """

    email: EmailStr
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None
    override_existing: bool = False


@router.post("/{identity_id}/email")
async def set_primary_email(
    identity_id: int,
    body: SetEmailBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Operator-driven manual fill of ``identity.primary_email``.

    Writes both the identity row (so the cold-outreach skill's
    ``primary_email`` check passes) and the matching provenance facts
    (``identity.email``, ``identity.email_source=manual``,
    ``identity.email_discovered_at``) so the audit trail shows where
    the value came from.

    Concurrency:
    * 409 ``email_already_set`` if a non-empty primary_email exists and
      ``override_existing`` is false — keeps a misclick from silently
      stomping a previously verified address.
    * Always last-writer-wins on the actual write; an in-flight
      discovery skill that finishes after this point will see the
      non-empty primary_email and skip its own write (per skill SOP).
    """
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "email format looks invalid",
        )

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")
    existing = str(ident.get("primary_email") or "").strip()
    if existing and not body.override_existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "email_already_set",
                "message": (
                    "this KOL already has a primary_email on file. "
                    "Re-submit with override_existing=true if you "
                    "really want to replace it."
                ),
                "current_email": existing,
            },
        )

    handle = ident.get("primary_handle")
    if not handle:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "identity has no primary_handle on file — refusing to upsert",
        )
    payload: dict[str, Any] = {
        "primary_handle": handle,
        "platform": ident.get("platform") or "instagram",
        "primary_email": email,
        "env": body.env,
    }
    try:
        await bridge.upsert_identity(payload)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": {
                    "identity.email": email,
                    "identity.email_source": "manual",
                    "identity.email_discovered_at": now_iso,
                    "identity.email_set_by": user["email"],
                },
                "source": f"web:{user['email']}",
                "env": body.env,
                "campaign_id": body.campaign_id,
            },
        )
    except BridgeError as exc:
        # primary_email is already in the identity row at this point;
        # don't fail the whole call just because provenance facts
        # couldn't be persisted. Surface as a 207-style payload.
        write_audit(
            conn,
            actor_user_id=user["id"],
            action="kol.email.set_partial",
            target=str(identity_id),
            payload={"email": email, "facts_error": str(exc)},
        )
        return {
            "ok": True,
            "email": email,
            "warning": f"identity updated but provenance facts failed: {exc}",
        }

    # Timeline event so the detail page shows the operator action.
    try:
        await bridge.write_event(
            {
                "identity_id": identity_id,
                "campaign_id": body.campaign_id,
                "event_type": "identity.email_set_manual",
                "actor": f"web:{user['email']}",
                "payload": {"email": email, "previous_email": existing or None},
                "env": body.env,
            },
        )
    except BridgeError:
        # Audit-only event; don't surface bridge errors here.
        pass

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.email.set",
        target=str(identity_id),
        payload={"email": email, "override": bool(existing)},
    )
    return {"ok": True, "email": email, "previous_email": existing or None}


class DiscoverEmailBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


_DISCOVER_EMAIL_INSTRUCTIONS = (
    "You are running the `kol-email-discovery` skill for ONE specific\n"
    "KOL identity at the request of the web console operator. Do NOT\n"
    "loop over any other identity in this campaign.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    "- kol-ops-bridge base URL: http://127.0.0.1:8080/api/plugins/kol-ops-bridge\n"
    "- Bridge auth header: X-Bridge-Key: $HERMES_KOL_OPS_BRIDGE_KEY\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- ALL CAL writes go through the deterministic CLI:\n"
    f"    python {_REPO_ROOT}/plugins/kol-ops-bridge/\n"
    "      scripts/kol_bridge_tool.py <cmd> --env <env> ...\n"
    "\n"
    "## Pipeline\n"
    "1. Read the identity row. If `primary_email` is already non-empty,\n"
    "   stop immediately and report `{skipped: \"already_has_email\"}`.\n"
    "   Do NOT overwrite an existing email under any circumstance.\n"
    "2. Invoke the `kol-email-discovery` skill with the supplied\n"
    "   identity_id, env, and campaign_id. The skill resolves the\n"
    "   email from public web sources (link-in-bio, personal site,\n"
    "   media kit) and writes `primary_email` via `upsert-identity`\n"
    "   plus provenance facts via `write-facts-multi`.\n"
    "3. On hit, report `{found: true, email, source, tier}`. On miss,\n"
    "   report `{found: false, tried: [...]}` and open a\n"
    "   `contact_email_not_found` escalation so the operator sees\n"
    "   exactly which sources were checked.\n"
    "4. Never invent an email address; heuristic guesses are\n"
    "   explicitly forbidden by the skill SOP.\n"
)


def _compose_discover_email_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
) -> str:
    lines = [
        "# kol_email_discovery",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    if campaign_id:
        lines.append(f"campaign_id: {campaign_id}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Resolve the outreach email for the single identity_id "
            "above by invoking `kol-email-discovery`. Do NOT loop "
            "over any other identity. On miss, open a "
            "`contact_email_not_found` escalation with the `tried` "
            "list so the operator can audit which sources you checked."
        ),
    ])
    return "\n".join(lines)


@router.post("/{identity_id}/discover-email")
async def discover_email(
    identity_id: int,
    body: DiscoverEmailBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Kick off the kol-email-discovery skill for one identity.

    Async — the gateway run takes 30–120 s while the agent crawls
    public surfaces (link-in-bio, personal site, media kit). The
    resulting ``primary_email`` and provenance facts land in CAL when
    the run completes; the detail page's poll loop will surface them
    on the next refresh.

    Concurrency:
    * 409 ``already_has_email`` if the identity already has a
      non-empty primary_email. (The skill itself short-circuits the
      same way; we just save the round-trip.)
    * 409 ``discover_email_inflight`` if another discovery was fired
      for this (identity, env) in the last ``INFLIGHT_TTL_SECONDS`` —
      durable across page reloads via product_campaign_runs.
    """
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    existing = str(ident.get("primary_email") or "").strip()
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "already_has_email",
                "message": (
                    "this KOL already has a primary_email on file. "
                    "Edit it manually instead of re-running discovery."
                ),
                "current_email": existing,
            },
        )

    # Per-identity TTL dedup. We intentionally don't gate on
    # ``_campaign_run_in_flight`` here — discovery is a single-identity
    # web-crawl run, not a campaign-mutating run, and blocking it
    # behind every approve cycle would feel unresponsive.
    dedup_key = f"discover-email:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "discover_email_inflight",
                "message": (
                    f"an email discovery run for this KOL was triggered "
                    f"in the last {INFLIGHT_TTL_SECONDS}s — wait for "
                    f"it to land (typically 30–120s) before retrying"
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )

    ensure_gateway_bridge_key()
    handle = ident.get("primary_handle")
    brief = _compose_discover_email_brief(
        identity_id=identity_id,
        handle=handle if isinstance(handle, str) else None,
        env=env,
        campaign_id=body.campaign_id,
        actor_email=user["email"],
    )
    try:
        run = await gateway.start_run(
            input=brief,
            instructions=_DISCOVER_EMAIL_INSTRUCTIONS,
            # Per-identity namespace; not a campaign-wide resume.
            session_id=f"kol-email-discover:{env}:{identity_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id and body.campaign_id:
        # Registering requires a campaign_id; for ad-hoc discovery
        # without a campaign, skip registration (dedup falls back to
        # the operator's local React state).
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-email-discover:{env}:{identity_id}",
            dedup_key=dedup_key,
        )
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.email.discover",
        target=str(identity_id),
        payload={"env": env, "campaign_id": body.campaign_id, "run_id": run_id},
    )
    return {
        "ok": True,
        "run_id": run_id,
        "identity_id": identity_id,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Social-link enrichment — manual entry + skill-driven discovery
# ---------------------------------------------------------------------------
#
# Cross-platform profile URLs (TikTok / YouTube / Facebook / X / Threads /
# Linktree / personal site) drive the "快速跳转" bar on the detail page.
# IG is normally captured during instagram-kol-discovery; the others are
# either side-effects of kol-email-discovery (when it browses link-in-bio
# pages) or filled by the operator / the dedicated discovery skill below.

ALLOWED_SOCIAL_LINK_KEYS: tuple[str, ...] = (
    "identity.instagram_profile_url",
    "identity.tiktok_profile_url",
    "identity.youtube_profile_url",
    "identity.facebook_profile_url",
    "identity.twitter_profile_url",
    "identity.threads_profile_url",
    "identity.linktree_url",
    "identity.personal_site_url",
)

_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)


class SetSocialLinkBody(CampaignIdNormaliserMixin):
    """Body for ``POST /kols/{identity_id}/social-link``.

    ``fact_key`` must be one of ``ALLOWED_SOCIAL_LINK_KEYS`` — keeps the
    endpoint from being a generic identity-namespace fact writer (which
    would belong to FactsEditor, not here).
    """

    fact_key: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=4, max_length=2000)
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None
    override_existing: bool = False


@router.post("/{identity_id}/social-link")
async def set_social_link(
    identity_id: int,
    body: SetSocialLinkBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Operator-driven manual fill of a single social-platform URL.

    Writes the URL fact + provenance (``<key>_source=manual``,
    ``<key>_discovered_at``) so the detail page shows where the value
    came from. Refuses to overwrite a non-empty existing value unless
    ``override_existing`` is true — symmetric with ``set_primary_email``.
    """
    if body.fact_key not in ALLOWED_SOCIAL_LINK_KEYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "code": "unknown_social_key",
                "message": f"fact_key must be one of {ALLOWED_SOCIAL_LINK_KEYS}",
            },
        )
    url = body.url.strip()
    if not _URL_RE.match(url):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "url must start with http:// or https:// and contain no whitespace",
        )

    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    # Check existing fact (campaign_id=None for reusable scope so the
    # check matches the write below).
    try:
        existing_facts = await bridge.read_facts(
            identity_id, campaign_id=None, env=body.env,
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    facts_map = (existing_facts or {}).get("facts") or {}
    existing_url = str(facts_map.get(body.fact_key) or "").strip()
    if existing_url and not body.override_existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "social_link_already_set",
                "message": (
                    f"{body.fact_key} is already set. Re-submit with "
                    "override_existing=true to replace it."
                ),
                "current_url": existing_url,
            },
        )

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    source_key = f"{body.fact_key}_source"
    at_key = f"{body.fact_key}_discovered_at"
    try:
        await bridge.write_facts(
            identity_id,
            {
                "namespace": "identity",
                "facts": {
                    body.fact_key: url,
                    source_key: "manual",
                    at_key: now_iso,
                },
                "source": f"web:{user['email']}",
                "env": body.env,
                # campaign_id=None makes this a reusable identity fact —
                # the next campaign for this KOL will inherit the URL.
                "campaign_id": None,
            },
        )
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.social_link.set",
        target=str(identity_id),
        payload={
            "fact_key": body.fact_key,
            "url": url,
            "override": bool(existing_url),
        },
    )
    return {"ok": True, "fact_key": body.fact_key, "url": url}


class DiscoverSocialLinksBody(CampaignIdNormaliserMixin):
    env: str = Field(default="LIVE", pattern="^(LIVE|TEST)$")
    campaign_id: str | None = None


_DISCOVER_SOCIAL_LINKS_INSTRUCTIONS = (
    "You are running the `kol-social-link-discovery` skill for ONE\n"
    "specific KOL identity at the request of the web console operator.\n"
    "Do NOT loop over any other identity in this campaign.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    "- kol-ops-bridge base URL: http://127.0.0.1:8080/api/plugins/kol-ops-bridge\n"
    "- Bridge auth header: X-Bridge-Key: $HERMES_KOL_OPS_BRIDGE_KEY\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- ALL CAL writes go through the deterministic CLI:\n"
    f"    python {_REPO_ROOT}/plugins/kol-ops-bridge/\n"
    "      scripts/kol_bridge_tool.py <cmd> --env <env> ...\n"
    "\n"
    "## Pipeline\n"
    "1. Read the identity row and the current identity-namespace facts.\n"
    "   For each target fact key in {instagram, tiktok, youtube, facebook,\n"
    "   twitter, threads, linktree, personal_site}_profile_url that is\n"
    "   already non-empty, treat it as resolved and DO NOT overwrite.\n"
    "2. If every target key is already resolved, stop immediately and\n"
    "   report `{skipped: \"already_has_all_social_links\"}`.\n"
    "3. Invoke `kol-social-link-discovery` with the supplied identity_id,\n"
    "   env, and campaign_id. The skill resolves missing URLs from public\n"
    "   sources (link-in-bio, personal site, IG/Facebook bio, media kit)\n"
    "   and writes each newly resolved URL plus its provenance facts via\n"
    "   `write-facts-multi` with campaign_id=null.\n"
    "4. On hit, report `{found: true, resolved: [...]}`. On total miss,\n"
    "   report `{found: false, tried: [...]}`. Do NOT open an escalation\n"
    "   — missing social URLs are non-blocking for outreach.\n"
    "5. Never invent URLs; heuristic guesses (e.g. instagram.com/handle\n"
    "   when you never verified the page belongs to the creator) are\n"
    "   forbidden by the skill SOP.\n"
)


def _compose_discover_social_links_brief(
    *,
    identity_id: int,
    handle: str | None,
    env: str,
    campaign_id: str | None,
    actor_email: str,
) -> str:
    lines = [
        "# kol_social_link_discovery",
        f"identity_id: {identity_id}",
        f"mode: {env}",
        f"requested_by: {actor_email}",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    if campaign_id:
        lines.append(f"campaign_id: {campaign_id}")
    lines.extend([
        "",
        "# required_next_step",
        (
            "Resolve the missing social-platform profile URLs for the "
            "single identity_id above by invoking "
            "`kol-social-link-discovery`. Do NOT loop over any other "
            "identity. Skip any target key that is already non-empty. "
            "On total miss, return the `tried` list — do NOT escalate."
        ),
    ])
    return "\n".join(lines)


@router.post("/{identity_id}/discover-social-links")
async def discover_social_links(
    identity_id: int,
    body: DiscoverSocialLinksBody,
    bridge: Annotated[BridgeClient, Depends(get_bridge)],
    gateway: Annotated[GatewayClient, Depends(get_gateway)],
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    """Kick off the kol-social-link-discovery skill for one identity.

    Async — the gateway run takes 30–120s while the agent crawls public
    surfaces. New URL facts land in CAL when the run completes; the
    detail page's poll loop surfaces them on the next refresh.

    Concurrency:
    * 409 ``discover_social_links_inflight`` if another discovery was
      fired for this (identity, env) in the last ``INFLIGHT_TTL_SECONDS``.
    * Unlike ``discover_email``, no "already has X" 409 — the skill
      short-circuits internally when all target keys are filled, and an
      operator may legitimately want to re-run to pick up a new platform
      they expect to exist.
    """
    env = body.env
    try:
        ident = await bridge.get_identity(identity_id)
    except BridgeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if not ident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "identity not found")

    dedup_key = f"discover-social-links:{env}:{identity_id}"
    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "discover_social_links_inflight",
                "message": (
                    f"a social-link discovery run for this KOL was triggered "
                    f"in the last {INFLIGHT_TTL_SECONDS}s — wait for it to "
                    f"land (typically 30–120s) before retrying"
                ),
                "run_id": inflight.get("run_id"),
                "started_at": inflight.get("started_at"),
            },
        )

    ensure_gateway_bridge_key()
    handle = ident.get("primary_handle")
    brief = _compose_discover_social_links_brief(
        identity_id=identity_id,
        handle=handle if isinstance(handle, str) else None,
        env=env,
        campaign_id=body.campaign_id,
        actor_email=user["email"],
    )
    try:
        run = await gateway.start_run(
            input=brief,
            instructions=_DISCOVER_SOCIAL_LINKS_INSTRUCTIONS,
            session_id=f"kol-social-link-discover:{env}:{identity_id}",
        )
    except GatewayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if isinstance(run_id, str) and run_id and body.campaign_id:
        register_run(
            conn,
            campaign_id=body.campaign_id,
            env=env,
            run_id=run_id,
            kind="draft",
            session_id=f"kol-social-link-discover:{env}:{identity_id}",
            dedup_key=dedup_key,
        )
    write_audit(
        conn,
        actor_user_id=user["id"],
        action="kol.social_links.discover",
        target=str(identity_id),
        payload={"env": env, "campaign_id": body.campaign_id, "run_id": run_id},
    )
    return {
        "ok": True,
        "run_id": run_id,
        "identity_id": identity_id,
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
