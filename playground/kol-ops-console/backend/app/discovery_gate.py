"""Shared helpers for the KOL discovery quantity-gate.

This module owns the rediscover composition + trigger logic so both the
public ``/rediscover`` HTTP handler (in ``routers/campaigns.py``) and the
post-terminal auto-retry hook (in ``routers/products.py``) can use it
without creating a circular import. ``routers/products.py → campaigns.py``
is intentionally avoided today; auto-retry would require it, so we keep
the shared logic here instead.

Behavior summary:
- After a discovery/rediscover agent run terminates, the console compares
  the persisted candidate count against ``product_campaigns.target_floor``.
  If short and ``retry_count < 3``, fire another rediscover automatically
  (counted toward retry_count). If still short after 3 auto-retries, open
  a ``discovery_floor_unmet`` escalation in CAL.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .bridge_runtime import ensure_gateway_bridge_key
from .campaign_locks import campaign_lock
from .gateway_client import GatewayClient, GatewayError
from .run_registry import get_inflight_run, register_run


logger = logging.getLogger(__name__)


_REPO_ROOT = str(Path(__file__).resolve().parents[4])


MAX_AUTO_RETRIES = 3
"""Hard cap on automatic post-terminal rediscover runs per campaign generation.

Counts ONLY auto-retries; operator-initiated /start and /rediscover do not
consume retry budget. Resets to 0 on operator-initiated runs.
"""


REDISCOVERY_INSTRUCTIONS = (
    "You are extending an existing KOL outreach campaign by discovering\n"
    "ADDITIONAL candidates on top of the pool that is already persisted\n"
    "in CAL. The web operator already reviewed the previous round and\n"
    "asked for more candidates.\n"
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
    "\n"
    "## Pipeline (run in order, do NOT skip)\n"
    "1. SKIP kol-campaign-intake. campaign_config is already persisted; do\n"
    "   NOT call upsert-campaign and do NOT overwrite any existing config.\n"
    "2. Read the current candidate pool from CAL FIRST:\n"
    "   `list-candidates --env <env> --campaign-id <id>`. Build an\n"
    "   exclusion set of every handle currently in the pool, regardless of\n"
    "   candidate_status (new/selected_for_outreach/rejected/archived).\n"
    "   Merge this set with the `already_discovered_handles` block in the\n"
    "   brief — trust whichever is larger.\n"
    "3. `skill_view(name='instagram-kol-discovery')` and then EXECUTE\n"
    "   discovery using the built-in BrowserUse tools — `browser_navigate`,\n"
    "   `browser_snapshot`, `browser_get_images`, `browser_click`,\n"
    "   `browser_type`, `vision_analyze`. Do NOT use the\n"
    "   `mcp_chrome_devtools_*` family.\n"
    "\n"
    "   ITERATION CONTRACT — HARD QUANTITY FLOOR (read carefully):\n"
    "   - The goal is to PERSIST at least `additional_target_count` NEW\n"
    "     candidates (handles not in the exclusion set). This is a HARD\n"
    "     FLOOR, not a soft target.\n"
    "   - The discovery skill's default browse budget is sized for a fresh\n"
    "     campaign. In rediscover mode you MUST keep iterating: after each\n"
    "     persistence round, re-check `list-candidates` and decide whether\n"
    "     the floor has been hit. If not, START ANOTHER discovery pass\n"
    "     with broadened or shifted keywords (different niche angles,\n"
    "     regional tags, language tags, adjacent hashtags, related-account\n"
    "     graph from already-qualified KOLs).\n"
    "   - Disqualifying a profile (off-niche, audience too small, no\n"
    "     contact) does NOT count toward the floor. Only persisted\n"
    "     `add-candidate` rows count.\n"
    "   - Budget yourself up to MAX(40, additional_target_count * 4)\n"
    "     profile visits per pass. Try at least 3 distinct keyword angles\n"
    "     before considering yourself blocked.\n"
    "   - Stopping short is a FAILURE STATE. The console runs a post-\n"
    "     terminal quantity gate: if persisted NEW candidates <\n"
    "     additional_target_count AND auto-retry budget remains, the\n"
    "     backend AUTO-FIRES another /rediscover for the same campaign_id\n"
    "     (up to 3 auto-retries total = 4 runs max). After that, the\n"
    "     operator gets a `discovery_floor_unmet` escalation. Therefore:\n"
    "     finishing partial is acceptable ONLY when truly blocked (rate\n"
    "     limits, niche exhausted, bridge/gateway down).\n"
    "   - When you stop short you MUST include in the final answer the\n"
    "     following two structured lines so the backend can decide between\n"
    "     auto-retry and early escalation:\n"
    "       floor_unmet_reason: <one-sentence why>\n"
    "       attempted_angles:\n"
    "         - <keyword/angle 1>\n"
    "         - <keyword/angle 2>\n"
    "         - <keyword/angle 3>\n"
    "4. Persist each NEW candidate IMMEDIATELY as you qualify it. For every\n"
    "   newly qualified profile, perform this deterministic sequence before\n"
    "   browsing for the next profile:\n"
    "   a) `upsert-identity --env <env> --json @/tmp/identity.json`;\n"
    "   b) `write-facts` or `write-facts-multi` for followers, region,\n"
    "      email/contact, creator type, evidence URL, and fit notes;\n"
    "   c) `add-candidate --env <env> --campaign-id <id> --json\n"
    "      @/tmp/candidate.json`;\n"
    "   d) `list-candidates --env <env> --campaign-id <id>` and verify the\n"
    "      handle is now present.\n"
    "   NEVER touch existing candidates: do NOT change their\n"
    "   candidate_status, do NOT re-add an excluded handle, do NOT call\n"
    "   `select-candidates` (the operator owns approval).\n"
    "5. After the new candidates are persisted, call\n"
    "   `resolve-relationships --env <env> --campaign-id <id>`. The bridge\n"
    "   side is idempotent — already-resolved candidates are untouched.\n"
    "6. STOP. Do NOT shortlist, draft emails, send mail, or touch the\n"
    "   approved KOLs from earlier rounds. The operator will review the\n"
    "   expanded pool in the web console.\n"
    "\n"
    "## Final-answer contract\n"
    "Report the count of NEW candidates persisted in this run (from your\n"
    "second `list-candidates` minus the size of the exclusion set), the\n"
    "additional_target_count from the brief, and the run's CAL totals.\n"
    "If you stopped short, include `floor_unmet_reason` and `attempted_angles`\n"
    "as specified in the iteration contract.\n"
    "\n"
    "## Failure handling\n"
    "- If `list-candidates` returns 0 BEFORE step 2, treat the brief's\n"
    "  `already_discovered_handles` as authoritative.\n"
    "- If the bridge returns 401, the X-Bridge-Key header is missing —\n"
    "  re-issue via the CLI (which reads HERMES_KOL_OPS_BRIDGE_KEY) or\n"
    "  add `--bridge-key $HERMES_KOL_OPS_BRIDGE_KEY` explicitly.\n"
    "- If a path returns 404, you almost certainly forgot the\n"
    "  `/api/plugins/kol-ops-bridge/` prefix or used port 8765 (console)\n"
    "  instead of 8080 (bridge).\n"
    "- On 3 consecutive identical failures, STOP and open an escalation\n"
    "  via `kol_bridge_tool.py open-escalation` rather than looping.\n"
)


_VALID_BROWSER_MODES = {"cloud", "local-chrome"}


def _resolve_browser_mode() -> str:
    """Mirror of ``routers/campaigns._resolve_browser_mode`` — kept here so
    auto-retry briefs do not depend on the routers module.
    """
    raw = (os.environ.get("KOL_BROWSER_MODE") or "").strip().lower()
    if raw in _VALID_BROWSER_MODES:
        return raw
    return "local-chrome"


def _compose_rediscover_brief(
    *,
    campaign_id: str,
    env: str,
    product: sqlite3.Row,
    additional_count: int,
    excluded_handles: list[str],
    test_mode_to: str | None,
) -> str:
    """Brief for any rediscover run (operator-initiated or auto-retry).

    Campaign_config is already persisted in CAL and must NOT be re-upserted.
    The agent only needs the rediscover directive + enough product context
    to derive search keywords.
    """
    tags = json.loads(product["tags_json"] or "[]")
    lines = [
        "# campaign_config (read-only — already in CAL, do NOT upsert)",
        f"campaign_id: {campaign_id}",
        f"product_sku: {product['sku']}",
        f"product_name: {product['name']}",
        f"mode: {env}",
        f"browser_mode: {_resolve_browser_mode()}",
        "triggered_by: web",
        "operation: rediscover",
    ]
    if test_mode_to:
        lines.append(f"test_mode_to: {test_mode_to}")
    if product["url"]:
        lines.append(f"product_url: {product['url']}")
    if tags:
        lines.append(f"product_tags: {', '.join(tags)}")
    if product["notes"]:
        lines.extend(["product_notes:", product["notes"]])

    lines.extend([
        "",
        "# rediscover_directive",
        f"additional_target_count: {additional_count}",
        (
            "already_discovered_handles: []"
            if not excluded_handles
            else "already_discovered_handles:"
        ),
    ])
    for handle in excluded_handles:
        lines.append(f"  - {handle}")

    pitch = (product["pitch_md"] or "").strip()
    if pitch:
        lines.extend([
            "",
            "# product_pitch (markdown - feed to KOL discovery)",
            pitch,
        ])
    selling_points = (product["selling_points"] or "").strip()
    if selling_points:
        lines.extend(["", "# selling_points", selling_points])
    return "\n".join(lines)


def _recover_test_mode_to(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    current: str | None,
    override: str | None = None,
) -> str | None:
    """Resolve a usable ``test_mode_to`` for TEST campaigns.

    Returns the first non-empty source among ``current`` (row), ``override``
    (caller-supplied), and the most recent ``campaign.start`` audit payload.
    Backfills ``product_campaigns.test_mode_to`` when recovered from audit.
    """
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


async def _excluded_handles_from(
    bridge: BridgeClient, candidates: list[dict[str, Any]]
) -> list[str]:
    """Project a list of candidate rows down to normalized exclusion handles.

    Falls back to ``bridge.get_identity`` when ``primary_handle`` is missing
    on the candidate row.
    """
    excluded: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        handle = cand.get("primary_handle")
        if not (isinstance(handle, str) and handle.strip()):
            iid = cand.get("identity_id")
            if isinstance(iid, int):
                try:
                    ident = await bridge.get_identity(iid)
                except BridgeError:
                    ident = {}
                handle = ident.get("primary_handle") if isinstance(ident, dict) else None
        if not (isinstance(handle, str) and handle.strip()):
            continue
        norm = handle.strip().lstrip("@").lower()
        if norm in seen:
            continue
        seen.add(norm)
        excluded.append(norm)
    return excluded


def _count_visible_candidates(candidates: list[dict[str, Any]]) -> int:
    """Count candidates visible to the operator (excludes rejected/archived).

    This is BOTH the operator-facing pool size (used by the UI for the
    "candidate_count" / "pending_candidate_count" badges) AND the
    authoritative metric for the discovery quantity gate. Approval moves a
    candidate from ``new`` to ``selected_for_outreach`` but the row stays
    visible, so this metric is decoupled from operator approvals — clicking
    Approve mid-rediscover does NOT depress the gate's current count and
    therefore does NOT cause a spurious auto-retry. Rejection / archival
    DOES depress the count, which is intentional (operator is saying "these
    don't count, find more").
    """
    return sum(
        1
        for c in candidates
        if isinstance(c, dict)
        and c.get("candidate_status") not in {"rejected", "archived"}
    )


# Historical name kept as an alias so external callers (and tests written
# against the old semantics) do not break. The gate itself uses
# ``_count_visible_candidates`` directly now.
_count_uncontacted_candidates = _count_visible_candidates


_UNMET_RE = re.compile(
    r"floor_unmet_reason\s*[:=]\s*(.+?)(?:\n|$)", re.IGNORECASE
)


def _extract_unmet_reason(output: Any) -> str | None:
    """Best-effort scan for a ``floor_unmet_reason:`` line in the agent's
    final answer. The discovery and rediscover prompts contract requires
    this line when stopping short.
    """
    if not output:
        return None
    if isinstance(output, (dict, list)):
        try:
            text = json.dumps(output, ensure_ascii=False)
        except (TypeError, ValueError):
            return None
    else:
        text = str(output)
    m = _UNMET_RE.search(text)
    if not m:
        return None
    # Trim wrapping quotes / JSON delimiters that survive when the agent's
    # output is serialized as JSON instead of plain markdown.
    reason = m.group(1).strip().strip("`\"',}] ")
    return reason or None


async def _trigger_rediscover_internal(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    product: sqlite3.Row,
    campaign_id: str,
    env: str,
    additional_count: int,
    test_mode_to_override: str | None,
    current_test_mode_to: str | None,
    rediscovery_instructions: str,
    actor: dict | None,
    is_auto_retry: bool = False,
    new_retry_count: int | None = None,
) -> dict[str, Any]:
    """Compose brief + start gateway run + register run + audit + update
    product_campaigns row.

    Used by both the public ``/rediscover`` endpoint (``is_auto_retry=False``,
    ``actor=user``) and the gate auto-retry hook (``is_auto_retry=True``,
    ``actor=None``).

    Does NOT enforce ``_campaign_run_in_flight`` — callers do that pre-check
    because they have different conflict-resolution semantics (HTTP 409 vs
    silent skip).

    Returns a dict with at least ``ok``, ``run_id``, ``additional_count``.
    On dedup-skip (auto-retry only) returns ``{"ok": False, "skipped": ...}``.
    """
    if is_auto_retry and new_retry_count is None:
        raise ValueError("new_retry_count is required when is_auto_retry=True")

    if is_auto_retry:
        dedup_key = f"auto-retry:{env}:{campaign_id}:{new_retry_count}"
    else:
        dedup_key = f"rediscover:{env}:{campaign_id}"

    inflight = get_inflight_run(conn, dedup_key=dedup_key)
    if inflight is not None:
        return {
            "ok": False,
            "skipped": "rediscover_inflight",
            "run_id": inflight["run_id"],
            "started_at": inflight["started_at"],
        }

    try:
        candidates_snapshot = await bridge.list_candidates(campaign_id, env=env)
    except BridgeError:
        candidates_snapshot = []
    excluded_handles = await _excluded_handles_from(bridge, candidates_snapshot)

    test_mode_to = _recover_test_mode_to(
        conn,
        campaign_id=campaign_id,
        env=env,
        current=current_test_mode_to,
        override=test_mode_to_override,
    )
    if env == "TEST" and not test_mode_to:
        # In the public endpoint this raises HTTPException; for auto-retry we
        # surface a structured skip so the caller can log/escalate without
        # crashing the GET that triggered the gate check.
        return {
            "ok": False,
            "skipped": "test_mode_to_missing",
            "campaign_id": campaign_id,
            "env": env,
        }

    brief_text = _compose_rediscover_brief(
        campaign_id=campaign_id,
        env=env,
        product=product,
        additional_count=additional_count,
        excluded_handles=excluded_handles,
        test_mode_to=test_mode_to,
    )

    ensure_gateway_bridge_key()
    out = await gateway.start_run(
        input=brief_text,
        instructions=rediscovery_instructions,
        session_id=f"kol-campaign:{env}:{campaign_id}",
    )

    new_run_id = out.get("run_id") if isinstance(out, dict) else None
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

    # Snapshot baseline + target_floor at run start so the post-terminal
    # gate has authoritative values. Both use the VISIBLE-pool metric
    # (includes ``selected_for_outreach``; excludes ``rejected``/``archived``)
    # so that operator approvals made mid-run do NOT depress ``current`` and
    # therefore do NOT trigger spurious auto-retries. Rejection during a run
    # still counts against the floor, which is intentional.
    #
    # ``gate_run_id`` is set to the new run_id so that ``_sync_run_states``
    # only dispatches the gate when this specific run terminates — not when
    # an unrelated approve-driven outreach run finishes on the same row.
    baseline_now = _count_visible_candidates(candidates_snapshot)

    if is_auto_retry:
        # Preserve target_floor; just refresh baseline + retry_count + run_id.
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running', "
            "started_at=?, baseline_candidate_count=?, retry_count=?, "
            "floor_unmet_reason=NULL, gate_run_id=? "
            "WHERE campaign_id=? AND env=?",
            (new_run_id, now, baseline_now, new_retry_count, new_run_id,
             campaign_id, env),
        )
    else:
        target_floor = baseline_now + additional_count
        conn.execute(
            "UPDATE product_campaigns SET run_id=?, status='running', "
            "started_at=?, target_floor=?, baseline_candidate_count=?, "
            "retry_count=0, floor_unmet_reason=NULL, gate_run_id=? "
            "WHERE campaign_id=? AND env=?",
            (new_run_id, now, target_floor, baseline_now, new_run_id,
             campaign_id, env),
        )

    if isinstance(new_run_id, str) and new_run_id:
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=new_run_id,
            kind="outreach",
            session_id=f"kol-campaign:{env}:{campaign_id}",
            dedup_key=dedup_key,
        )

    actor_user_id = actor["id"] if isinstance(actor, dict) and "id" in actor else None
    write_audit(
        conn,
        actor_user_id=actor_user_id,
        action=(
            "campaign.auto_rediscover" if is_auto_retry else "campaign.rediscover"
        ),
        target=campaign_id,
        payload={
            "env": env,
            "additional_count": additional_count,
            "excluded_handle_count": len(excluded_handles),
            "run_id": new_run_id,
            "is_auto_retry": is_auto_retry,
            "retry_count": new_retry_count if is_auto_retry else 0,
        },
    )
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "env": env,
        "run_id": new_run_id,
        "additional_count": additional_count,
        "excluded_handle_count": len(excluded_handles),
        "is_auto_retry": is_auto_retry,
        "retry_count": new_retry_count if is_auto_retry else 0,
    }


def _clear_gate_run_id(
    conn: sqlite3.Connection, *, campaign_id: str, env: str
) -> None:
    conn.execute(
        "UPDATE product_campaigns SET gate_run_id=NULL "
        "WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    )


async def evaluate_gate_after_terminal(
    *,
    bridge: BridgeClient,
    gateway: GatewayClient,
    conn: sqlite3.Connection,
    campaign_id: str,
    env: str,
    target_floor: int,
    retry_count: int,
    run_info: dict[str, Any] | None,
    rediscovery_instructions: str,
    gate_run_id: str | None = None,
) -> dict[str, Any]:
    """Post-terminal quantity-gate evaluator.

    Called from ``_sync_run_states`` when the **discovery-purpose** run for a
    campaign reaches terminal state. Approve-driven outreach runs do NOT
    trigger this — the caller distinguishes via ``product_campaigns.gate_run_id``.
    ``cancelled`` discovery runs are intentionally not gated (operator
    stopped the run on purpose); ``_sync_run_states`` clears ``gate_run_id``
    in that case without calling this function.

    Behavior:
    - ``current >= target_floor`` → pass, clear ``gate_run_id``, no-op.
    - ``current < target_floor and retry_count < MAX_AUTO_RETRIES`` → fire a
      rediscover for the missing count, incrementing ``retry_count``.
      ``gate_run_id`` is updated by the trigger to the new auto-retry's
      run_id.
    - ``current < target_floor and retry_count >= MAX_AUTO_RETRIES`` → open
      a ``discovery_floor_unmet`` escalation, persist ``floor_unmet_reason``,
      clear ``gate_run_id``.

    Returns a small status dict for logging — the caller does not act on it.
    All bridge/gateway errors are swallowed (logged) so a GET request cannot
    fail because of gate side-effects.

    ``current`` uses the visible-pool metric (everything except
    rejected/archived), so operator approvals made between trigger and
    terminal do NOT depress the count.
    """
    # Per-campaign lock serializes the gate's "check → spawn → update"
    # sequence against operator-initiated /rediscover and against the
    # multi-GET race where two concurrent ``_sync_run_states`` callers
    # observe the same running→terminal flip.
    lock = await campaign_lock(env, campaign_id)
    async with lock:
        # Re-check ``gate_run_id`` under the lock — another concurrent
        # gate evaluation may have already cleared it (or replaced it
        # with a new auto-retry's run). If gate_run_id no longer matches
        # the run we were called for, someone else already handled this
        # terminal flip; skip.
        if gate_run_id is not None:
            row = conn.execute(
                "SELECT gate_run_id FROM product_campaigns "
                "WHERE campaign_id=? AND env=?",
                (campaign_id, env),
            ).fetchone()
            if row is None or row["gate_run_id"] != gate_run_id:
                return {"ok": True, "outcome": "skipped_stale_gate_run_id"}

        try:
            candidates = await bridge.list_candidates(campaign_id, env=env)
        except BridgeError as exc:
            logger.warning(
                "gate: list_candidates failed for %s/%s: %s",
                campaign_id, env, exc,
            )
            return {"ok": False, "skipped": "list_candidates_failed"}

        current = _count_visible_candidates(candidates)
        if current >= target_floor:
            _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
            return {"ok": True, "outcome": "floor_met", "current": current,
                    "target_floor": target_floor}

        # Resolve the product row for brief composition.
        row = conn.execute(
            "SELECT sku, test_mode_to FROM product_campaigns "
            "WHERE campaign_id=? AND env=?",
            (campaign_id, env),
        ).fetchone()
        if row is None:
            logger.warning(
                "gate: campaign row missing for %s/%s", campaign_id, env
            )
            _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
            return {"ok": False, "skipped": "campaign_row_missing"}

        product = conn.execute(
            "SELECT sku, name, url, tags_json, notes, pitch_md, "
            "selling_points, variants_json FROM products WHERE sku=?",
            (row["sku"],),
        ).fetchone()
        if product is None:
            logger.warning("gate: product row missing for sku=%s", row["sku"])
            _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
            return {"ok": False, "skipped": "product_row_missing"}

        reason = _extract_unmet_reason(
            run_info.get("output") if isinstance(run_info, dict) else None
        )

        if retry_count >= MAX_AUTO_RETRIES:
            final_reason = reason or "max_auto_retries_exceeded"
            conn.execute(
                "UPDATE product_campaigns SET floor_unmet_reason=? "
                "WHERE campaign_id=? AND env=?",
                (final_reason, campaign_id, env),
            )
            try:
                await bridge.open_escalation({
                    "env": env,
                    "campaign_id": campaign_id,
                    "reason": "discovery_floor_unmet",
                    "question": (
                        f"Discovery floor not met after {retry_count} "
                        f"auto-retries: have {current}/{target_floor}. "
                        f"Reason: {final_reason}."
                    ),
                })
            except BridgeError as exc:
                logger.warning(
                    "gate: open_escalation failed for %s/%s: %s",
                    campaign_id, env, exc,
                )
                # Keep gate_run_id so a subsequent sync retries the
                # escalation when the bridge is back.
                return {"ok": False, "skipped": "escalation_failed",
                        "current": current, "target_floor": target_floor}
            _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
            return {"ok": True, "outcome": "escalated", "current": current,
                    "target_floor": target_floor, "reason": final_reason}

        additional = max(1, target_floor - current)
        try:
            out = await _trigger_rediscover_internal(
                bridge=bridge,
                gateway=gateway,
                conn=conn,
                product=product,
                campaign_id=campaign_id,
                env=env,
                additional_count=additional,
                test_mode_to_override=None,
                current_test_mode_to=row["test_mode_to"],
                rediscovery_instructions=rediscovery_instructions,
                actor=None,
                is_auto_retry=True,
                new_retry_count=retry_count + 1,
            )
        except GatewayError as exc:
            logger.warning(
                "gate: auto-retry gateway error for %s/%s: %s",
                campaign_id, env, exc,
            )
            return {"ok": False, "skipped": "gateway_error",
                    "current": current, "target_floor": target_floor}
        except Exception:
            logger.exception(
                "gate: auto-retry crashed for %s/%s", campaign_id, env,
            )
            return {"ok": False, "skipped": "crashed"}

        # Persist the reason (if any) on the row even on a successful retry,
        # so FE can render the last self-reported blocker while the new run
        # is up. ``_trigger_rediscover_internal`` already updated
        # ``gate_run_id`` to point at the auto-retry's new run.
        if reason:
            conn.execute(
                "UPDATE product_campaigns SET floor_unmet_reason=? "
                "WHERE campaign_id=? AND env=?",
                (reason, campaign_id, env),
            )

        return {"ok": True, "outcome": "auto_retry_fired", "current": current,
                "target_floor": target_floor, "rediscover_result": out}
