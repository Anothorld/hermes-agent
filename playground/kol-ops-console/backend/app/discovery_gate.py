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
  If short and ``retry_count < 5``, fire another rediscover automatically
  (counted toward retry_count). If still short after 5 auto-retries, open
  a ``discovery_floor_unmet`` escalation in CAL.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .audit import write_audit
from .bridge_client import BridgeClient, BridgeError
from .nox_gate import materialize_discovery_nox_config
from .bridge_runtime import ensure_gateway_bridge_key
from .campaign_locks import campaign_lock
from .gateway_client import GatewayClient, GatewayError, RUNNING_STATES
from .bridge_agent_contract_loader import (
    discovery_cli_rules,
    gateway_contract_for_brief,
    terminal_safety_rules,
)
from .config import get_settings
from .db import _connect
from .launch_accept import launch_or_accept, queue_would_block
from .launch_rollback import rollback_rediscover_failure
from .learned_criteria import learned_criteria_brief_section
from .sku_prior_approval import prior_sku_approved_handles
from .run_launch_queue import new_pending_run_id
from .run_registry import finalize_run_id, get_inflight_run, register_run


logger = logging.getLogger(__name__)


_REPO_ROOT = str(Path(__file__).resolve().parents[4])


MAX_AUTO_RETRIES = 5
"""Hard cap on automatic post-terminal rediscover runs per campaign generation.

Counts ONLY auto-retries; operator-initiated /start and /rediscover do not
consume retry budget. Resets to 0 on operator-initiated runs.
"""

MAX_CONSECUTIVE_ZERO_NEW_RUNS = 2
"""Early-escalation threshold: after this many consecutive auto-retries that
produce ZERO new persisted candidates, escalate to the operator instead of
firing another auto-retry. Avoids burning the full ``MAX_AUTO_RETRIES`` budget
when the niche is clearly exhausted (e.g. SSF8033 on 2026-07-01 ran 5 zero-new
retries costing ~5.2M tokens before escalation).

A "zero-new" run is one whose ``persisted_count_at_end`` equals the previous
round's ``persisted_count_at_end`` (no net-new candidates survived). The
operator can still manually /rediscover after early escalation if they want
to try a different angle.
"""


REDISCOVERY_INSTRUCTIONS = (
    "You are extending an existing KOL outreach campaign by discovering\n"
    "ADDITIONAL candidates on top of the pool that is already persisted\n"
    "in CAL. The web operator already reviewed the previous round and\n"
    "asked for more candidates.\n"
    "\n"
    "## Runtime contract (MEMORIZE before any tool call)\n"
    f"{gateway_contract_for_brief(compact=True)}\n"
    f"{terminal_safety_rules(repo_root=_REPO_ROOT)}\n"
    f"{discovery_cli_rules()}\n"
    f"- Repo root for file tools is {_REPO_ROOT}.\n"
    "- CLI failures print JSON on **stdout**. Empty output + exit 2 → read\n"
    "  stdout for `error`/`hint`; never fall back to execute_code.\n"
    "- Guard block (`source: kol_bridge_agent_guard`) is NOT bridge validation —\n"
    "  fix the terminal command per `hint`.\n"
    "- Invalid subcommands: read-identity → get-identity; list-campaigns →\n"
    "  get-campaign. `--pretty` is global before subcommand.\n"
    "- Do NOT read or search `plugins/kol-ops-bridge/` for API discovery.\n"
    "- Use the **terminal** tool for `kol_bridge_tool.py` (not execute_code+subprocess).\n"
    "- Ingest JSON shape: `skills/social-media/instagram-kol-discovery/references/"
    "bridge-cli-json-payloads.md` (nested source/identity/candidate — NOT flat handle).\n"
    "\n"
    "## Pipeline (run in order, do NOT skip)\n"
    "0. If the brief contains `# resume_directives`, complete STEP_0 there\n"
    "   FIRST — pending ingests from prior round(s) before ANY new discovery.\n"
    "   For each listed handle not already in the exclusion set: rebuild\n"
    "   `/tmp/ingest_<handle>.json`, run `ingest-confirmed-candidate`, verify\n"
    "   via `list-candidates`. Do NOT start hashtag/browser exploration until\n"
    "   STEP_0 is done or every pending handle is confirmed in CAL.\n"
    "1. SKIP kol-campaign-intake. campaign_config is already persisted; do\n"
    "   NOT call upsert-campaign and do NOT overwrite any existing config.\n"
    "2. Read the current candidate pool from CAL FIRST:\n"
    "   `list-candidates --env <env> --campaign-id <id>` (print to terminal stdout —\n"
    "   NEVER `> /tmp/...`; redirect empties stdout and looks like CAL failure).\n"
    "   Or `list-candidate-handles` for a compact handle-only view.\n"
    "   Build an\n"
    "   exclusion set of every handle currently in the pool, regardless of\n"
    "   candidate_status (new/selected_for_outreach/rejected/archived).\n"
    "   Merge this set with the `already_discovered_handles` block in the\n"
    "   brief — both mean **already persisted in CAL**; trust whichever is\n"
    "   larger. Do NOT re-ingest handles that appear in either set. Also merge\n"
    "   handles from `list-outreach-cooldown-handles --env <env> --plain` (14-day\n"
    "   cross-campaign outreach cooldown) so rediscover never re-adds a\n"
    "   recently contacted KOL.\n"
    "3. `skill_view(name='instagram-kol-discovery')` and then EXECUTE\n"
    "   discovery using built-in `browser_*` on local debug Chrome —\n"
    "   `browser_navigate`, `browser_snapshot`, `browser_get_images`,\n"
    "   `browser_click`, `browser_type`, `vision_analyze`. Do NOT call\n"
    "   `delegate_task` — browse and persist in THIS run. Do NOT use the\n"
    "   `mcp_chrome_devtools_*` family.\n"
    "   **Browser no-hang:** one page at a time, single attempt per URL.\n"
    "   Navigate/snapshot error or timeout → switch surface; never retry the\n"
    "   same URL in a loop. A partial floor is acceptable; a hung run is not.\n"
    "   Do not fan out parallel browser sessions in one run.\n"
    "   When the brief includes `nox_discovery_enabled: true` and\n"
    "   `campaign_config_file:`, run the Nox audience screen per the\n"
    "   discovery skill (after profile pre-check, before Reel deep dive).\n"
    "   Use `--gate discovery_qualify --dimensions audience` only.\n"
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
    "     contact) does NOT count toward the floor. Only successful\n"
    "     `ingest-confirmed-candidate` rows count.\n"
    "   - Budget yourself up to MAX(40, additional_target_count * 4)\n"
    "     profile visits per pass. Try at least 3 distinct keyword angles\n"
    "     before considering yourself blocked.\n"
    "   - Stopping short is a FAILURE STATE. The console runs a post-\n"
    "     terminal quantity gate: if persisted NEW candidates <\n"
    "     additional_target_count AND auto-retry budget remains, the\n"
    "     backend AUTO-FIRES another /rediscover for the same campaign_id\n"
    "     (up to 5 auto-retries total = 6 runs max). After that, the\n"
    "     operator gets a `discovery_floor_unmet` escalation. Therefore:\n"
    "     finishing partial is acceptable ONLY when truly blocked (rate\n"
    "     limits, niche exhausted, bridge/gateway down).\n"
    "   - When you stop short you MUST include in the final answer the\n"
    "     structured diagnostics block (see instagram-kol-discovery skill):\n"
    "       floor_unmet_reason: <one-sentence why>\n"
    "       attempted_angles:\n"
    "         - <keyword/angle 1>\n"
    "       pending_ingests: (required for every qualified-but-not-ingested handle)\n"
    "         - \"<handle> — <why not ingested>\"\n"
    "       next_round_focus: (exploration queue after pending ingests)\n"
    "         - \"<handle/seed> — <why>\"\n"
    "4. Persist each NEW candidate IMMEDIATELY as you qualify it. For every\n"
    "   newly qualified profile, BEFORE browsing the next profile:\n"
    "   a) Write `/tmp/ingest_<handle>.json` with nested `source`, `identity`,\n"
    "      `candidate`, and `identity_facts` (see bridge-cli-json-payloads.md);\n"
    "   b) `ingest-confirmed-candidate --campaign-id <id> --env <env> --json\n"
    "      @/tmp/ingest_<handle>.json`;\n"
    "   c) `list-candidates --env <env> --campaign-id <id>` and verify the\n"
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
    "If you stopped short, include `floor_unmet_reason`, `attempted_angles`,\n"
    "`pending_ingests` (if any), and `next_round_focus` as in the skill.\n"
    "Do NOT use a prose-only \"Next round should:\" list — the console parser\n"
    "requires the YAML field names above.\n"
    "\n"
    "## Failure handling\n"
    "- Terminal tool returned ~45 chars with empty `output` and exit 0 → you\n"
    "  redirected bridge stdout with `> file` or piped through head/grep/jq.\n"
    "  Re-run without redirect/pipe; read full JSON from terminal stdout.\n"
    "- Guard block JSON with `source: kol_bridge_agent_guard` is NOT bridge\n"
    "  validation — fix the command per `hint`.\n"
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
    prior_diagnostics: list[dict[str, Any]] | None = None,
    nox_cfg_path: str = "",
) -> str:
    """Brief for any rediscover run (operator-initiated or auto-retry).

    Campaign_config is already persisted in CAL and must NOT be re-upserted.
    The agent only needs the rediscover directive + enough product context
    to derive search keywords.

    When ``prior_diagnostics`` is non-empty (i.e. earlier rounds of this
    same campaign generation have already terminated), their structured
    diagnostics are rendered as a ``# prior_runs`` block plus a
    ``# this_round_guidance`` block so the agent does not re-trace
    exhausted angles.
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
            else "already_discovered_handles:  # ALREADY IN CAL — skip ingest for every handle below"
        ),
    ])
    for handle in excluded_handles:
        lines.append(f"  - {handle}")

    pending_for_resume: list[str] = []
    if prior_diagnostics:
        lines.extend([
            "",
            "# prior_runs (read-only — earlier rounds this campaign generation)",
        ])
        for entry in prior_diagnostics:
            lines.append(
                f"## Round {entry.get('round_index', '?')} "
                f"(run_id={entry.get('run_id')}, "
                f"persisted={entry.get('persisted_count_at_end')}/"
                f"floor={entry.get('target_floor')}, "
                f"auto_retry={entry.get('is_auto_retry', False)})"
            )
            # Render next_round_focus FIRST (above scalars/other lists) so
            # the agent reading prior_runs immediately sees what the prior
            # round flagged for follow-up. Hard-capped to avoid next-round
            # brief bloat from a runaway agent.
            focus_items = entry.get("next_round_focus") or []
            if focus_items:
                lines.append("next_round_focus:")
                for item in focus_items[:_NEXT_ROUND_FOCUS_CAP]:
                    lines.append(f"  - {item}")
            for scalar_key in _DIAG_SCALAR_KEYS:
                value = entry.get(scalar_key)
                if value:
                    lines.append(f"{scalar_key}: {value}")
            for list_key in _DIAG_LIST_KEYS:
                if list_key in ("next_round_focus", "pending_ingests"):
                    continue  # rendered separately (focus at top; pending in resume_directives)
                items = entry.get(list_key) or []
                if items:
                    lines.append(f"{list_key}:")
                    for item in items:
                        lines.append(f"  - {item}")

        pending_for_resume = _collect_pending_ingests_for_resume(
            prior_diagnostics, excluded_handles
        )  # merged with precompress snapshot below

        lines.extend([
            "",
            "# this_round_guidance",
            "Read prior_runs and resume_directives above FIRST.",
            "0. If `# resume_directives` is present, complete STEP_0 (pending",
            "   ingests) before any browser_navigate or new seed exploration.",
            "1. Process the MOST RECENT round's next_round_focus list before",
            "   generating any new seeds. Each item is a concrete handle / seed /",
            "   reel the prior round flagged as worth digging into; treat them",
            "   as the highest-priority exploration queue AFTER pending ingests.",
            "2. Do NOT repeat any seed / hashtag / public-web query listed in",
            "   any prior round's attempted_angles or remediation_attempted",
            "   UNLESS that round's floor_unmet_reason was infrastructural",
            "   (rate_limit, cdp_lost, IG checkpoint, bridge/gateway down).",
            "   Content exhaustion (\"niche exhausted\", \"no new candidates\")",
            "   does NOT get retried with the same seeds.",
            "3. After working through next_round_focus, prioritize new seeds",
            "   that fill the most recent round's underserved_verticals.",
        ])

    # Merge precompress snapshot handles (visited-but-not-ingested captured
    # by kol-discovery-precompress-guard before the last context compression)
    # into the resume queue. Works even when prior_diagnostics is empty — a
    # first-run compression can still leave pending handles.
    snapshot_handles = _read_precompress_snapshot(env, campaign_id)
    excluded_lower = {_normalize_handle(h) for h in excluded_handles}
    snap_pending: list[str] = []
    seen_handles: set[str] = set()
    for h in snapshot_handles:
        norm = _normalize_handle(h)
        if norm in excluded_lower or norm in seen_handles:
            continue
        seen_handles.add(norm)
        snap_pending.append(h)
    # Merge snapshot handles into pending_for_resume (dedup by handle).
    if snap_pending:
        existing_handles = {
            _normalize_handle(_handle_from_pending_item(item))
            for item in pending_for_resume
        }
        for h in snap_pending:
            norm = _normalize_handle(h)
            if norm not in existing_handles:
                pending_for_resume.append(h)
                existing_handles.add(norm)
    if pending_for_resume:
        lines.extend(_render_resume_directives_block(pending_for_resume))

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
    if nox_cfg_path:
        lines.extend([
            "",
            "nox_discovery_enabled: true",
            f"campaign_config_file: {nox_cfg_path}",
        ])
    # Hard rules inlined into the brief so they are seen even when the
    # agent's skill_view cache serves a stale SKILL.md. These mirror the
    # P0 skill rules from instagram-kol-discovery/SKILL.md but are
    # guaranteed fresh because they ride in the run input, not a cache.
    lines.extend([
        "",
        "# hard_rules (override any cached skill version — obey verbatim)",
        "1. Bootstrap the exclusion set with:",
        "   list-candidate-handles --env <env> --campaign-id <cid> --with-status --plain",
        "   Treat EVERY handle in the output as off-limits for browser_navigate.",
        "2. Before writing /tmp/ingest_<handle>.json, self-check the payload:",
        "   - creator brief bundle: all 6 keys present together or all absent",
        "     (content_pillars, signature_hooks, voice_descriptors, hero_post_url,",
        "     hero_post_note, recommendation_reason).",
        "   - voice_descriptors: a list of 2-3 items.",
        "   - hero_post_url: canonical instagram.com/reel/<id>/ or /p/<id>/.",
        "   - recommendation_reason: include _source, _discovered_at, _discovered_url.",
        "   - hero_post_url_discovered_url: the creator's instagram profile URL.",
        "   Fix the JSON in-place before calling ingest-confirmed-candidate.",
        "3. Visit-conclusion rule (hard): every handle you browser_navigate to",
        "   must end with exactly one of:",
        "   (a) ingest-confirmed-candidate success,",
        "   (b) listed in pending_ingests in the run summary, or",
        "   (c) an explicit visited_handles: YAML entry with 'DISCARD: <reason>'.",
        "   No visited-but-undecided handles may remain at run end.",
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
    """Project candidate rows to normalized exclusion handles."""
    ids = [
        int(c["identity_id"])
        for c in candidates
        if isinstance(c, dict) and isinstance(c.get("identity_id"), int)
    ]
    brief_map = (
        await bridge.batch_identity_briefs(ids) if ids else {}
    )
    excluded: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        iid = cand.get("identity_id")
        ident = (
            brief_map.get(int(iid)) if isinstance(iid, int) else None
        ) or {}
        handle = (
            ident.get("primary_handle")
            if isinstance(ident, dict)
            else cand.get("primary_handle")
        )
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


_DIAG_SCALAR_KEYS = (
    "floor_unmet_reason",
    "diversity_floor_unmet",
    "active_range",
    "active_range_source",
)

_DIAG_LIST_KEYS = (
    "attempted_angles",
    "underserved_verticals",
    "remediation_attempted",
    "vertical_coverage",
    # Agent's concrete suggestions for what the next round should dig
    # into FIRST — handles, hashtags, seeds, or reels to verify. Each item
    # follows the format ``<handle/seed> — <why this is worth prioritizing>``
    # per SKILL.md contract; capped at 10 items by the composer to avoid
    # next-round brief bloat.
    "next_round_focus",
    # Qualified handles not yet ingested into CAL — each item
    # ``<handle> — <why not ingested>``; drives # resume_directives on retry.
    "pending_ingests",
    # Every IG handle visited via browser_navigate this run + its terminal
    # state (ingested / DISCARD: <reason> / pending_ingests: <reason>).
    # Enforces the SKILL.md "visit-conclusion rule" — no visited-but-
    # undecided gray zone. Operator-auditable; also recovered by the
    # kol-discovery-precompress-guard plugin when compression cuts a run
    # short. See SKILL.md "Visit-conclusion rule (hard)".
    "visited_handles",
)

_NEXT_ROUND_FOCUS_CAP = 10
_PENDING_INGESTS_CAP = 5

_DIAG_ALL_KEYS = _DIAG_SCALAR_KEYS + _DIAG_LIST_KEYS

_UNPERSISTED_SIGNAL_RE = re.compile(
    r"(?i)(qualified\s+but\s+unpersisted|not\s+yet\s+persisted|pending\s+ingest)",
)
_UNPERSISTED_SECTION_RE = re.compile(
    r"(?is)"
    r"(?:#{1,3}\s*)?(?:\*\*)?qualified\s+but\s+unpersisted(?:\*\*)?[^\n]*\n"
    r"(.*?)"
    r"(?=^#{1,3}\s|\n---\s*\n|\*\*floor_unmet|\nfloor_unmet_reason:|\Z)",
    re.MULTILINE,
)
# Heuristic bullets must look like KOL handles (**handle** or @handle), not seed phrases.
_HANDLE_BULLET_RE = re.compile(
    r"^\s*[-*]\s+(?:\*\*@?([a-zA-Z0-9._]{2,40})\*\*|@([a-zA-Z0-9._]{2,40}))"
    r"(?:\s|$|—|-)",
    re.MULTILINE,
)

_DIAG_SCALAR_RE = re.compile(
    r"^\s*(" + "|".join(_DIAG_SCALAR_KEYS) + r")\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _coerce_output_to_text(output: Any) -> str:
    if not output:
        return ""
    if isinstance(output, (dict, list)):
        try:
            return json.dumps(output, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    return str(output)


def _normalize_handle(raw: str) -> str:
    return raw.strip().lstrip("@").lower()


def _handle_from_pending_item(item: str) -> str:
    """Extract handle from ``handle — reason`` pending_ingests line."""
    head = item.split("—", 1)[0].split(" - ", 1)[0].strip()
    head = head.strip("*").strip()
    return _normalize_handle(head)


def _extract_pending_ingests_heuristic(text: str) -> list[str] | None:
    """Best-effort parse of prose unpersisted sections (Round 8-style output)."""
    if not _UNPERSISTED_SIGNAL_RE.search(text):
        return None
    section = ""
    m = _UNPERSISTED_SECTION_RE.search(text)
    if m:
        section = m.group(1)
    # No broad fallback: "NOT yet persisted" in a title plus attempted_angles
    # bullets would false-positive on seed lines (e.g. "dadrianca cluster").
    if not section.strip():
        return None
    items: list[str] = []
    seen: set[str] = set()
    for hm in _HANDLE_BULLET_RE.finditer(section):
        handle = _normalize_handle(hm.group(1) or hm.group(2) or "")
        if not handle or handle in seen:
            continue
        seen.add(handle)
        items.append(
            f"{handle} — qualified prior round, not ingested; "
            "rebuild ingest JSON per bridge-cli-json-payloads.md"
        )
        if len(items) >= _PENDING_INGESTS_CAP:
            break
    return items or None


def _merge_pending_ingests(
    explicit: list[str] | None, heuristic: list[str] | None
) -> list[str] | None:
    """Combine explicit YAML and heuristic items; explicit wins ordering."""
    merged: list[str] = []
    seen: set[str] = set()
    for batch in (explicit or [], heuristic or []):
        for item in batch:
            handle = _handle_from_pending_item(item)
            if not handle or handle in seen:
                continue
            seen.add(handle)
            merged.append(item.strip())
            if len(merged) >= _PENDING_INGESTS_CAP:
                return merged
    return merged or None


def _read_precompress_snapshot(env: str, campaign_id: str) -> list[str]:
    """Read handles visited-but-not-ingested captured by the
    ``kol-discovery-precompress-guard`` plugin before the last context
    compression. Returns an empty list when no snapshot exists or it is
    unreadable. The snapshot is written to ``/tmp/precompress_pending_<sid>.json``
    where ``<sid>`` is the session id with non-alphanumeric chars replaced
    by ``_`` — matching ``plugins/kol-discovery-precompress-guard/hooks.py``.
    """
    sid = f"kol-campaign:{env}:{campaign_id}"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", sid)
    path = os.path.join(tempfile.gettempdir(), f"precompress_pending_{safe}.json")
    try:
        data = json.loads(Path(path).read_text())
        handles = data.get("pending_handles") or []
        return [str(h).strip() for h in handles if str(h).strip()]
    except Exception:
        return []


def _collect_pending_ingests_for_resume(
    prior_diagnostics: list[dict[str, Any]] | None,
    excluded_handles: list[str],
) -> list[str]:
    """Aggregate pending ingests from prior rounds, excluding handles already in CAL."""
    excluded = {_normalize_handle(h) for h in excluded_handles}
    out: list[str] = []
    seen: set[str] = set()
    rounds = prior_diagnostics or []
    batches: list[list[str]] = []
    if rounds:
        latest = rounds[-1].get("pending_ingests") or []
        if latest:
            batches.append(latest)
    for entry in reversed(rounds[:-1]):
        older = entry.get("pending_ingests") or []
        if older:
            batches.append(older)
    for batch in batches:
        for item in batch:
            if not isinstance(item, str) or not item.strip():
                continue
            handle = _handle_from_pending_item(item)
            if handle in excluded or handle in seen:
                continue
            seen.add(handle)
            out.append(item.strip())
            if len(out) >= _PENDING_INGESTS_CAP:
                return out
    return out


def _render_resume_directives_block(pending_items: list[str]) -> list[str]:
    lines = [
        "",
        "# resume_directives (HARD — before any browser_navigate)",
        f"pending_ingest_count: {len(pending_items)}",
        "pending_ingests:",
    ]
    for item in pending_items:
        lines.append(f"  - {item}")
    lines.extend([
        "STEP_0: For EACH handle above not in list-candidates exclusion set:",
        "  (Skip if handle is in `already_discovered_handles` — already in CAL.)",
        "  browser_navigate profile if needed → write /tmp/ingest_<handle>.json",
        "  (nested source/identity/candidate per bridge-cli-json-payloads.md)",
        "  → ingest-confirmed-candidate → list-candidates verify",
        "  → then continue discovery for additional_target_count.",
        "Do NOT skip STEP_0 to start new hashtag exploration.",
    ])
    return lines


def _parse_yaml_list(text: str, key: str) -> list[str] | None:
    pat = re.compile(
        rf"^{re.escape(key)}\s*:\s*\n((?:[ \t]+-[ \t]+.+(?:\n|$))+)",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return None
    items = re.findall(r"^[ \t]+-[ \t]+(.+?)\s*$", m.group(1), re.MULTILINE)
    return items or None


# Heuristic recovery for visited_handles when the agent omits the YAML
# block. Looks for @handle / `handle` mentions paired with visit/ingest/
# discard signals in the final answer prose.
_VISITED_HANDLE_RE = re.compile(
    r"(?:@([a-zA-Z0-9._]{2,40})|`([a-zA-Z0-9._]{2,40})`)"
)
_DISCARD_SIGNAL_RE = re.compile(
    r"(?i)\b(DISCARD|not\s+qualified|不符合|skip|below\s+\d|already\s+(in|persisted)|"
    r"不\s*可\s*ingest|门槛|cooldown|excluded|reject)"
)
_INGESTED_SIGNAL_RE = re.compile(
    r"(?i)\b(ingest(?:ed)?|candidate_id|persisted|入库|持久化)"
)


def _extract_visited_handles_heuristic(text: str) -> list[str] | None:
    """Recover visited handles from final-answer prose when the agent did
    not emit a ``visited_handles:`` YAML block.

    Scans for @handle / ``handle`` mentions near DISCARD or ingest signals
    and returns entries in the SKILL.md ``<handle> — <conclusion>`` form so
    the diagnostics consumer can treat them uniformly. Returns ``None`` when
    nothing plausible is found.
    """
    if not text:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for m in _VISITED_HANDLE_RE.finditer(text):
        handle = (m.group(1) or m.group(2) or "").lower().rstrip(".")
        if not handle or handle in seen:
            continue
        # Snippet window around the mention to classify the conclusion.
        start = max(0, m.start() - 120)
        end = min(len(text), m.end() + 120)
        window = text[start:end]
        if _DISCARD_SIGNAL_RE.search(window):
            conclusion = "DISCARD: heuristic (disqualify signal in context)"
        elif _INGESTED_SIGNAL_RE.search(window):
            conclusion = "ingested: heuristic (ingest signal in context)"
        else:
            # Mention only, no clear conclusion — still record so the
            # visit-conclusion rule can flag it as undecided next round.
            conclusion = "undecided: heuristic (no conclusion signal found)"
        seen.add(handle)
        out.append(f"{handle} — {conclusion}")
    return out or None


def _extract_run_diagnostics(output: Any) -> dict[str, Any]:
    """Best-effort scan for SKILL-contract diagnostic fields in the agent's
    final answer. Returns a dict with all known keys present; any field the
    agent did not emit is ``None``.
    """
    diag: dict[str, Any] = {k: None for k in _DIAG_ALL_KEYS}
    text = _coerce_output_to_text(output)
    if not text:
        return diag
    for m in _DIAG_SCALAR_RE.finditer(text):
        key = m.group(1).lower()
        if diag.get(key):
            continue  # first match wins
        # Trim wrapping quotes / trailing commas that survive when the
        # agent output is JSON-serialized. Preserve brackets so values like
        # ``active_range: [0.30, 0.60]`` round-trip intact.
        diag[key] = m.group(2).strip().strip("`\"', ") or None
    for key in _DIAG_LIST_KEYS:
        diag[key] = _parse_yaml_list(text, key)
    if not diag.get("pending_ingests"):
        diag["pending_ingests"] = _extract_pending_ingests_heuristic(text)
    else:
        diag["pending_ingests"] = _merge_pending_ingests(
            diag.get("pending_ingests"), _extract_pending_ingests_heuristic(text)
        )
    # Recover visited_handles from prose when the agent omitted the YAML
    # block, so the visit-conclusion rule still has an auditable trail.
    if not diag.get("visited_handles"):
        diag["visited_handles"] = _extract_visited_handles_heuristic(text)
    return diag


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

    logger.info(
        "launch_path=trigger_rediscover campaign=%s env=%s auto_retry=%s retry_count=%s",
        campaign_id, env, is_auto_retry,
        new_retry_count if is_auto_retry else 0,
    )

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
    prior_sku_handles = await prior_sku_approved_handles(
        conn,
        bridge,
        sku=str(product["sku"]),
        env=env,
        exclude_campaign_id=campaign_id,
    )
    if prior_sku_handles:
        merged = list(excluded_handles)
        seen = {h.lower() for h in merged}
        for handle in prior_sku_handles:
            norm = handle.strip().lstrip("@").lower()
            if norm and norm not in seen:
                seen.add(norm)
                merged.append(norm)
        excluded_handles = merged

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

    prior_diagnostics = _read_diagnostics_history(
        conn, campaign_id=campaign_id, env=env
    )

    nox_cfg_path = await materialize_discovery_nox_config(
        bridge, campaign_id, env=env
    )
    brief_text = _compose_rediscover_brief(
        campaign_id=campaign_id,
        env=env,
        product=product,
        additional_count=additional_count,
        excluded_handles=excluded_handles,
        test_mode_to=test_mode_to,
        prior_diagnostics=prior_diagnostics,
        nox_cfg_path=nox_cfg_path,
    )
    learned_section = await learned_criteria_brief_section(
        bridge, sku=product["sku"], env=env,
    )
    if learned_section:
        brief_text = f"{brief_text}\n{learned_section}"

    ensure_gateway_bridge_key()
    session_id = f"kol-campaign:{env}:{campaign_id}"

    async def _start_rediscover() -> dict[str, Any]:
        return await gateway.start_run(
            input=brief_text,
            instructions=rediscovery_instructions,
            session_id=session_id,
        )

    baseline_now = _count_visible_candidates(candidates_snapshot)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    # Auto-retry must use the same async-accept + callback wiring as
    # operator /rediscover when the launch queue is busy; gating callbacks
    # on ``not is_auto_retry`` left campaigns stuck on ``pending:*`` ids.
    will_async_accept = (
        get_settings().launch_http_202
        and queue_would_block(session_id=session_id)
    )
    pending_run_id: str | None = (
        new_pending_run_id() if will_async_accept else None
    )
    prev_row = conn.execute(
        "SELECT run_id, status FROM product_campaigns "
        "WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    previous_run_id = (
        str(prev_row["run_id"]) if prev_row and prev_row["run_id"] else None
    )
    previous_status = (
        str(prev_row["status"]) if prev_row and prev_row["status"] else None
    )
    actor_user_id = actor["id"] if isinstance(actor, dict) and "id" in actor else None

    async def _on_rediscover_error(_exc: Exception) -> None:
        await rollback_rediscover_failure(
            campaign_id=campaign_id,
            env=env,
            pending_run_id=pending_run_id,
            previous_run_id=previous_run_id,
            previous_status=previous_status,
        )

    def _persist_run_row(run_id: str | None, *, db: sqlite3.Connection) -> None:
        if is_auto_retry:
            db.execute(
                "UPDATE product_campaigns SET run_id=?, status='running', "
                "started_at=?, baseline_candidate_count=?, retry_count=?, "
                "floor_unmet_reason=NULL, gate_run_id=? "
                "WHERE campaign_id=? AND env=?",
                (run_id, now, baseline_now, new_retry_count, run_id,
                 campaign_id, env),
            )
        else:
            target_floor = baseline_now + additional_count
            db.execute(
                "UPDATE product_campaigns SET run_id=?, status='running', "
                "started_at=?, target_floor=?, baseline_candidate_count=?, "
                "retry_count=0, floor_unmet_reason=NULL, gate_run_id=? "
                "WHERE campaign_id=? AND env=?",
                (run_id, now, target_floor, baseline_now, run_id,
                 campaign_id, env),
            )

    async def _on_rediscover_success(run: dict[str, Any], _result: Any) -> None:
        rid = run.get("run_id") if isinstance(run, dict) else None
        bg = _connect(get_settings().db_path)
        try:
            if pending_run_id and isinstance(rid, str) and rid:
                finalize_run_id(bg, pending_run_id=pending_run_id, actual_run_id=rid)
            _persist_run_row(rid if isinstance(rid, str) else None, db=bg)
            if isinstance(rid, str) and rid:
                register_run(
                    bg,
                    campaign_id=campaign_id,
                    env=env,
                    run_id=rid,
                    kind="outreach",
                    session_id=session_id,
                    dedup_key=dedup_key,
                )
            write_audit(
                bg,
                actor_user_id=actor_user_id,
                action=(
                    "campaign.auto_rediscover" if is_auto_retry
                    else "campaign.rediscover"
                ),
                target=campaign_id,
                payload={
                    "env": env,
                    "additional_count": additional_count,
                    "excluded_handle_count": len(excluded_handles),
                    "run_id": rid,
                    "is_auto_retry": is_auto_retry,
                    "retry_count": new_retry_count if is_auto_retry else 0,
                    "async_accept": True,
                },
            )
            bg.commit()
        finally:
            bg.close()

    try:
        accepted, out = await launch_or_accept(
            gateway,
            _start_rediscover,
            session_id=session_id,
            dedup_key=dedup_key,
            on_success=_on_rediscover_success if will_async_accept else None,
            on_error=_on_rediscover_error if will_async_accept else None,
            job_meta={
                "campaign_id": campaign_id,
                "env": env,
                "pending_run_id": pending_run_id,
            },
        )
    except GatewayError:
        raise

    if accepted:
        if out.get("deduped"):
            return {
                "ok": True,
                "accepted": True,
                "campaign_id": campaign_id,
                "env": env,
                "pending_run_id": out.get("pending_run_id") or pending_run_id,
                "additional_count": additional_count,
                "excluded_handle_count": len(excluded_handles),
                **out,
            }
        if not pending_run_id:
            pending_run_id = new_pending_run_id()
        _persist_run_row(pending_run_id, db=conn)
        register_run(
            conn,
            campaign_id=campaign_id,
            env=env,
            run_id=pending_run_id,
            kind="outreach",
            session_id=session_id,
            dedup_key=dedup_key,
        )
        return {
            "ok": True,
            "accepted": True,
            "campaign_id": campaign_id,
            "env": env,
            "pending_run_id": pending_run_id,
            "additional_count": additional_count,
            "excluded_handle_count": len(excluded_handles),
            **out,
        }

    new_run_id = out.get("run_id") if isinstance(out, dict) else None
    _persist_run_row(new_run_id if isinstance(new_run_id, str) else None, db=conn)

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


def _read_diagnostics_history(
    conn: sqlite3.Connection, *, campaign_id: str, env: str
) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT diagnostics_history FROM product_campaigns "
        "WHERE campaign_id=? AND env=?",
        (campaign_id, env),
    ).fetchone()
    raw = (row[0] if row else None) or "[]"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _count_consecutive_zero_new(diagnostics_history: list[dict[str, Any]]) -> int:
    """Count trailing rounds in ``diagnostics_history`` with no net-new
    persisted candidates.

    Walks from the end of the list backward; for each adjacent pair
    ``(entry[i], entry[i-1])`` where
    ``entry[i].persisted_count_at_end == entry[i-1].persisted_count_at_end``
    (i.e. the later round added zero candidates vs the prior round), increments
    the streak. Stops at the first round that DID grow the pool.

    Returns 0 when fewer than 2 entries exist (no streak possible) or when the
    most recent round grew the pool.

    ``diagnostics_history`` MUST already include the just-terminated round's
    entry (caller appends it via ``_append_diagnostics_entry`` before invoking
    this helper).
    """
    if len(diagnostics_history) < 2:
        return 0
    streak = 0
    for i in range(len(diagnostics_history) - 1, 0, -1):
        prev = diagnostics_history[i - 1]
        curr = diagnostics_history[i]
        prev_count = prev.get("persisted_count_at_end")
        curr_count = curr.get("persisted_count_at_end")
        if prev_count is None or curr_count is None:
            break
        if curr_count == prev_count:
            streak += 1
        else:
            break
    return streak


def _append_diagnostics_entry(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    env: str,
    gate_run_id: str | None,
    target_floor: int,
    persisted_count_at_end: int,
    retry_count: int,
    diagnostics: dict[str, Any],
) -> None:
    """Append one round's diagnostics snapshot to ``diagnostics_history``.

    Called for every terminal discovery/rediscover run, whether the floor
    was met or not, so future rounds (auto-retry or operator /rediscover)
    see the full per-generation trail.
    """
    prior = _read_diagnostics_history(conn, campaign_id=campaign_id, env=env)
    entry: dict[str, Any] = {
        "round_index": len(prior) + 1,
        "run_id": gate_run_id,
        "ended_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "target_floor": target_floor,
        "persisted_count_at_end": persisted_count_at_end,
        "is_auto_retry": retry_count > 0,
    }
    for key, value in diagnostics.items():
        if value is not None:
            entry[key] = value
    prior.append(entry)
    conn.execute(
        "UPDATE product_campaigns SET diagnostics_history=? "
        "WHERE campaign_id=? AND env=?",
        (json.dumps(prior, ensure_ascii=False), campaign_id, env),
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
        logger.info(
            "launch_path=gate_after_terminal campaign=%s env=%s retry=%s "
            "gate_run_id=%s current_floor_target=%s",
            campaign_id, env, retry_count, gate_run_id, target_floor,
        )
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

        # Parse structured diagnostics from the agent's final answer and
        # append them to diagnostics_history regardless of floor outcome,
        # so future rounds (auto-retry or operator /rediscover) inherit the
        # full per-generation trail of attempted_angles / vertical_coverage /
        # floor_unmet_reason / underserved_verticals / remediation_attempted.
        diagnostics = _extract_run_diagnostics(
            run_info.get("output") if isinstance(run_info, dict) else None
        )
        _append_diagnostics_entry(
            conn,
            campaign_id=campaign_id,
            env=env,
            gate_run_id=gate_run_id,
            target_floor=target_floor,
            persisted_count_at_end=current,
            retry_count=retry_count,
            diagnostics=diagnostics,
        )
        reason = diagnostics["floor_unmet_reason"]

        if current >= target_floor:
            _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
            return {"ok": True, "outcome": "floor_met", "current": current,
                    "target_floor": target_floor}

        # Early-escalation: if the last MAX_CONSECUTIVE_ZERO_NEW_RUNS rounds
        # all produced zero net-new persisted candidates, the niche is almost
        # certainly exhausted and another auto-retry will burn tokens for
        # nothing. Escalate to the operator now instead of firing retry N+1.
        # Only triggers for auto-retry rounds (retry_count > 0); the initial
        # operator-launched run is never early-escalated on its own.
        if retry_count > 0:
            history_after_append = _read_diagnostics_history(
                conn, campaign_id=campaign_id, env=env,
            )
            zero_new_streak = _count_consecutive_zero_new(history_after_append)
            if zero_new_streak >= MAX_CONSECUTIVE_ZERO_NEW_RUNS:
                early_reason = (
                    reason
                    or f"consecutive_zero_new_runs={zero_new_streak}"
                )
                conn.execute(
                    "UPDATE product_campaigns SET floor_unmet_reason=? "
                    "WHERE campaign_id=? AND env=?",
                    (early_reason, campaign_id, env),
                )
                try:
                    await bridge.open_escalation({
                        "env": env,
                        "campaign_id": campaign_id,
                        "reason": "discovery_floor_unmet",
                        "question_to_operator": (
                            f"连续 {zero_new_streak} 轮 auto-retry 0 新增候选，"
                            f"当前 {current}/{target_floor}。"
                            f"niche 可能已枯竭（排除集大 / surface 反复失败）。"
                            f"建议人工介入：放宽门槛 / 调整 driver / 清理排除集，"
                            f"或手动 /rediscover 换角度。原因：{early_reason}。"
                        ),
                    })
                except BridgeError as exc:
                    logger.warning(
                        "gate: early open_escalation failed for %s/%s: %s",
                        campaign_id, env, exc,
                    )
                    return {"ok": False, "skipped": "escalation_failed",
                            "current": current, "target_floor": target_floor}
                _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
                logger.info(
                    "gate: early-escalated %s/%s after %d consecutive zero-new "
                    "rounds (current=%d target=%d retry=%d)",
                    campaign_id, env, zero_new_streak, current,
                    target_floor, retry_count,
                )
                return {
                    "ok": True, "outcome": "early_escalation_zero_new",
                    "current": current, "target_floor": target_floor,
                    "zero_new_streak": zero_new_streak, "reason": early_reason,
                }

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
                    "question_to_operator": (
                        f"自动重试 {retry_count} 次后仍未达到发现下限："
                        f"当前 {current}/{target_floor}。原因：{final_reason}。"
                        "请决定是放宽条件、手动补量，还是暂停本活动发现。"
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
        # Defensive in-flight guard: even though we hold ``campaign_lock``
        # and re-checked ``gate_run_id``, an operator may have fired
        # ``/start`` or ``/rediscover`` in the window between the terminal
        # sync that called us and now. We check the row's ``run_id`` (NOT
        # ``gate_run_id`` — that's our own pointer and is always set here)
        # against the gateway: if the row's run_id differs from the
        # gate_run_id we were called for AND that run is non-terminal,
        # someone else launched something and we should not stack on top.
        op_row = conn.execute(
            "SELECT run_id FROM product_campaigns "
            "WHERE campaign_id=? AND env=?",
            (campaign_id, env),
        ).fetchone()
        op_run_id = str(op_row["run_id"]) if op_row and op_row["run_id"] else None
        if op_run_id and op_run_id != gate_run_id:
            try:
                op_info = await gateway.get_run(op_run_id)
            except GatewayError:
                op_info = None
            op_state = str((op_info or {}).get("status") or "").lower()
            if op_state in RUNNING_STATES:
                logger.info(
                    "gate: skipping auto-retry for %s/%s — independent "
                    "in-flight run (run_id=%s state=%s) at retry=%d",
                    campaign_id, env, op_run_id, op_state, retry_count,
                )
                _clear_gate_run_id(conn, campaign_id=campaign_id, env=env)
                return {
                    "ok": True, "outcome": "skipped_in_flight_on_terminal",
                    "current": current, "target_floor": target_floor,
                    "in_flight_run_id": op_run_id, "in_flight_state": op_state,
                }
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
