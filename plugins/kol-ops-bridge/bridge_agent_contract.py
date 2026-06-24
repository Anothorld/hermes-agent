"""Shared bridge agent contract — lint rules + gateway brief snippets.

Used by ``scripts/_cli_guardrails.py``, Console gateway instructions, and the
``kol-bridge-agent-guard`` Hermes plugin hook.
"""

from __future__ import annotations

import re
from typing import Iterable

CANONICAL_CLI_REL = "plugins/kol-ops-bridge/scripts/kol_bridge_tool.py"
NOX_TOOL_REL = "plugins/nox-kol-bridge/scripts/nox_kol_tool.py"
CLI_WRAPPER_REL = "plugins/kol-ops-bridge/scripts/kol-bridge-cli"
CLI_PYTHON = "python3"
CLI_INVOCATION = f"{CLI_PYTHON} {CANONICAL_CLI_REL}"
NOX_TOOL_INVOCATION = f"{CLI_PYTHON} {NOX_TOOL_REL}"
DISPATCH_CONTEXT_VIEW_AGENT = "agent"


def dispatch_context_cli_line(
    *,
    identity_id: int | str,
    campaign_id: str,
    env: str,
    view: str = DISPATCH_CONTEXT_VIEW_AGENT,
    repo_root: str | None = None,
) -> str:
    """Canonical ``get-dispatch-context`` invocation for agent gateway runs."""
    cli = gateway_cli_invocation(repo_root)
    view_flag = f" --view {view}" if view else ""
    return (
        f"{cli} get-dispatch-context --identity-id {identity_id} "
        f"--campaign-id {campaign_id} --env {env}{view_flag}"
    )


def cli_invocation_abs(repo_root: str) -> str:
    """Absolute ``python3 -u kol_bridge_tool.py`` for gateway terminal briefs."""
    from pathlib import Path

    tool = Path(repo_root).expanduser().resolve() / CANONICAL_CLI_REL
    return f"{CLI_PYTHON} -u {tool}"


def gateway_cli_invocation(repo_root: str | None = None) -> str:
    """CLI prefix for gateway terminal briefs — absolute when ``repo_root`` is set."""
    if repo_root:
        return cli_invocation_abs(repo_root)
    return CLI_INVOCATION

MEMORY_LAYERS_BRIEF_LINES: tuple[str, ...] = (
    "## Memory layers (four-store model)",
    "Email tactics and approved playbooks: cal_snapshot.dispatch_context.learning_hints (authoritative).",
    "Promoted playbooks: skill references/learned/<goal>.md when present.",
    "Repeat-KOL relationship: reusable_facts.facts.personalization_hint in dispatch context.",
    "Episodic cross-session context: Hindsight prefetch + hindsight_recall (advisory only).",
    "Profile meta: MEMORY.md / USER.md — not for tactics or policies.",
    "Priority on conflict: fact ownership > pricing engine > escalation/HARD rules > "
    "learning_hints > references/learned > personalization_hint > Hindsight > MEMORY.md.",
    "Do NOT hindsight_retain email templates or negotiation tactics — Console learning owns those.",
)


def memory_layers_brief_block() -> str:
    """Static gateway brief section for Hindsight + CAL memory boundaries."""
    return "\n".join(MEMORY_LAYERS_BRIEF_LINES)


CLASSIFIER_HANDOFF_BRIEF_LINES: tuple[str, ...] = (
    "## Classifier handoff (Step 2→2.5→3 — mandatory, no operator escalation for format errors)",
    "Load: skill_view kol-reply-dispatcher templates/classifier-handoff-checklist.md",
    " and kol-email-stage-classifier templates/classifier-output.json.",
    "Step 1.5: transform dispatch_context.goals[] → goals_map + current_goal_state lane map (templates/goals-shape-transform.md).",
    "Path A (preferred): inline classify → raw JSON only → parse → Step 2.5 sanitize-classifier-facts → Step 3 write-facts-multi same turn.",
    "Path B: delegate_task only with kol-email-stage-classifier/templates/delegate-task-context.md override; parse results[0].summary only.",
    "Before classify: get-parsed-escalation-rules --env <env>; pass summary into classifier escalation_rules input.",
    "Technical parse failure: retry ≤3 (inline preferred), then defer — never open-escalation for JSON format.",
    "After successful parse: never re-run Step 2; never read /tmp/classification_result.json.",
    "Step 4 select-draftable-plan: pass goals as name→row map (never the raw goals array).",
)


def classifier_handoff_brief_block() -> str:
    """Gateway brief snippet for kol-reply Step 2→3 handoff reliability."""
    return "\n".join(CLASSIFIER_HANDOFF_BRIEF_LINES)


def format_hindsight_recall_seed(
    *,
    campaign_id: str,
    identity_id: int | str,
    handle: str | None = None,
) -> str:
    """Per-KOL seed block so Hindsight auto_recall gets handle + campaign IDs."""
    lines = [
        "# hindsight_recall_seed",
        f"campaign_id: {campaign_id}",
        f"identity_id: {identity_id}",
    ]
    if handle:
        lines.append(f"handle: {handle}")
    lines.extend([
        "Use the IDs above when calling hindsight_recall for prior episodic context.",
        "Email tactics: learning_hints in cal_snapshot only (authoritative).",
    ])
    return "\n".join(lines)


AGENT_BRIDGE_CONTRACT_LINES: tuple[str, ...] = (
    "Bridge agent hard rules (mandatory):",
    f"1. CAL reads/writes: {CLI_INVOCATION} <subcommand> --env LIVE|TEST ...",
    "2. NEVER execute_code / curl / urllib / requests to :8080 .../kol-ops-bridge.",
    "3. NEVER hardcode BRIDGE_KEY or API secrets in code.",
    "4. NEVER read kol-ops-bridge source (plugin_api.py, serve.py, reply_draft.py, cal.py).",
    "5. NEVER search_files or read_file under plugins/kol-ops-bridge/ for API discovery.",
    "6. Use native terminal for CLI (one subcommand per call), not execute_code wrappers.",
    "7. Do NOT PATCH /escalations/{id} after Console resolve — return JSON envelope only.",
    "8. Dispatch reads: get-dispatch-context --view agent (slim bundle; omit lanes).",
    f"9. Nox API: {NOX_TOOL_INVOCATION} contacts|creator-search|... "
    f"(never kol-ops-bridge/scripts/nox_kol_tool.py — that path does not exist).",
)

# (code, pattern, hint)
_AGENT_LINT_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "hardcoded_bridge_key",
        re.compile(r"BRIDGE_KEY\s*=\s*['\"]", re.I),
        "Remove hardcoded keys; CLI reads HERMES_KOL_OPS_BRIDGE_KEY / secrets.yaml.",
    ),
    (
        "curl_bridge_http",
        re.compile(
            r"curl\b.*(?:kol-ops-bridge|/api/plugins/kol-ops-bridge)|"
            r"127\.0\.0\.1:8080[^\s\"']*kol-ops-bridge",
            re.I | re.S,
        ),
        f"Use {CLI_INVOCATION} subcommands instead of curl.",
    ),
    (
        "wrong_dispatch_curl_path",
        re.compile(
            r"kol-ops-bridge/(?:dispatch-context|facts/)(?!\s)",
            re.I,
        ),
        "Wrong URL shape. Use get-dispatch-context / get-facts with --identity-id.",
    ),
    (
        "patch_escalation_http",
        re.compile(
            r"(?:curl|urllib|requests\.\w+).*?/escalations/\d+",
            re.I | re.S,
        ),
        "Console already resolved the escalation. Do not PATCH it; write facts/events via CLI.",
    ),
    (
        "urllib_bridge",
        re.compile(
            r"urllib\.request\.Request\b.*kol-ops-bridge|"
            r"urlopen\b.*kol-ops-bridge",
            re.I | re.S,
        ),
        f"Use {CLI_INVOCATION} instead of hand-rolled urllib.",
    ),
    (
        "requests_bridge",
        re.compile(
            r"requests\.(?:get|post|put|patch)\b[^\n]*kol-ops-bridge",
            re.I,
        ),
        f"Use {CLI_INVOCATION} instead of requests.* to the bridge.",
    ),
    (
        "read_plugin_source",
        re.compile(
            r"open\s*\(\s*['\"][^'\"]*(?:plugin_api|reply_draft|serve)\.py",
            re.I,
        ),
        "Use persist-reply-draft --help or skill_view; do not read bridge source.",
    ),
    (
        "direct_cal_import",
        re.compile(r"(?:import\s+cal\b|from\s+cal\s+import|sqlite3.*cal\.db)", re.I),
        "Never open cal.db; use the bridge CLI/API only.",
    ),
    (
        "bridge_cli_via_execute_code",
        re.compile(
            r"subprocess\.(?:run|call|Popen)\b[^\n]*kol[_-]bridge_tool|"
            r"kol[_-]bridge_tool\.py[^\n]*subprocess",
            re.I | re.S,
        ),
        f"Run one {CLI_INVOCATION} command via the terminal tool, not execute_code+subprocess.",
    ),
    (
        "batch_ingest_files",
        re.compile(
            r"(?:"
            # Multiple ingest JSON paths in one Python list (execute_code batching)
            r"\[[^\]]*['\"]/tmp/ingest_[^'\"]+\.json['\"][^\]]*['\"]/tmp/ingest_"
            r"|"
            # Comma-separated ingest paths in one statement
            r"/tmp/ingest_[^'\"\s/]+\.json\s*,\s*['\"]?/?tmp/ingest_"
            r"|"
            # Loop driving more than one ingest call
            r"(?:for|while)\s+\w+\s+in\s+[^\n]+(?:ingest_|/tmp/ingest_)"
            r"|"
            # candidates[] then ingest-confirmed-candidate in same snippet
            r"candidates\s*=\s*\[[^\]]+\][\s\S]{0,400}?ingest-confirmed-candidate"
            r")",
            re.I,
        ),
        (
            "Ingest one handle at a time via terminal (not execute_code). "
            "This is the agent guard — not bridge JSON validation. "
            "Write @/tmp/ingest_<handle>.json then one ingest-confirmed-candidate call per handle."
        ),
    ),
    (
        "terminal_multi_ingest",
        re.compile(
            r"ingest-confirmed-candidate\b[^;\n]*(?:[;&]|&&|\|\|)[^;\n]*ingest-confirmed-candidate\b",
            re.I,
        ),
        (
            "One ingest-confirmed-candidate per terminal call — no `;`, `&&`, or `||` chains. "
            "Ingest handles sequentially in separate terminal invocations."
        ),
    ),
    (
        "explore_bridge_routes",
        re.compile(
            r"serve\.py.*route|Find (?:all )?(?:route|API)|/health\b.*kol-ops-bridge",
            re.I,
        ),
        "Do not explore bridge routes in source; use print-agent-contract or --help.",
    ),
    (
        "read_env_for_secrets",
        re.compile(
            r"open\s*\([^)]*\.env|"
            r"read_text\([^)]*\.env|"
            r"Path\([^)]*\.env",
            re.I,
        ),
        "Never read .env files for bridge keys; use kol_bridge_tool.py (inherits env).",
    ),
    (
        "write_facts_reply_draft",
        re.compile(
            r"write-facts(?:-multi)?\b[^\n]*approval\.reply_draft|"
            r"['\"]approval\.reply_draft['\"]",
            re.I,
        ),
        "Use persist-reply-draft or persist-initial-outreach-draft — not write-facts on approval.reply_draft.",
    ),
    (
        "terminal_cd_repo_root",
        re.compile(
            r"^\s*cd\s+[^\n;]*hermes-agent\s*(?:;|\s*$)",
            re.I | re.M,
        ),
        (
            "Do not `cd` into hermes-agent/ alone (loads AGENTS.md noise). "
            "Run kol-bridge-cli with absolute path from any cwd."
        ),
    ),
    (
        "bare_python_bridge",
        re.compile(
            r"(?:^|[;&|]\s*)python\s+(?:plugins/)?kol-ops-bridge/",
            re.I | re.M,
        ),
        "macOS has no bare `python`. Use absolute path to kol-bridge-cli (or python3 + absolute kol_bridge_tool.py).",
    ),
    (
        "relative_bridge_cli_path",
        re.compile(
            r"(?:^|[;&|]\s*)python3?\s+plugins/kol-ops-bridge/scripts/kol[_-]bridge",
            re.I | re.M,
        ),
        "Terminal cwd is often $HOME — relative plugins/... fails silently. "
        "Use absolute python3 -u .../kol_bridge_tool.py from the brief.",
    ),
    (
        "wrong_nox_tool_path",
        re.compile(r"kol-ops-bridge/scripts/nox_kol", re.I),
        f"Wrong Nox CLI path. Use {NOX_TOOL_INVOCATION} (nox-kol-bridge plugin).",
    ),
    (
        "python3_on_kol_bridge_cli_wrapper",
        re.compile(
            r"python3(?:\s+-u)?\s+[^\s;|&]*kol-bridge-cli\b",
            re.I,
        ),
        "kol-bridge-cli is a bash script — run python3 -u .../kol_bridge_tool.py directly "
        "(exact prefix is in the gateway brief).",
    ),
    (
        "redirect_bridge_stdout",
        re.compile(
            r"kol[_-]bridge(?:-cli|_tool\.py)\s+[^\n]*(?<![0-9])>\s*\S",
            re.I,
        ),
        (
            "Never redirect bridge CLI stdout with `> file` — the terminal tool only "
            "sees empty output (45-char wrapper) and it looks like CAL failure. "
            "Print JSON directly to terminal. `2>/dev/null` alone is OK; never `> /tmp/...`."
        ),
    ),
    (
        "pipe_bridge_stdout",
        re.compile(
            r"kol[_-]bridge(?:-cli|_tool\.py)\s+[^\n]*\|\s*(?:head|tail|grep|awk|sed|jq|python3?|cut)\b",
            re.I,
        ),
        (
            "Do not pipe bridge CLI output through head/grep/jq/python — read the full "
            "JSON line from terminal stdout. (`| tee` is OK when you still read stdout.)"
        ),
    ),
    (
        "invalid_subcommand_read_identity",
        re.compile(r"kol[_-]bridge(?:-cli|_tool\.py)\s+read-identity\b", re.I),
        "No read-identity subcommand. Use get-identity --identity-id <id> --env LIVE.",
    ),
    (
        "invalid_subcommand_list_campaigns",
        re.compile(r"kol[_-]bridge(?:-cli|_tool\.py)\s+list-campaigns\b", re.I),
        "No list-campaigns subcommand. Use get-campaign --campaign-id <id> --env LIVE.",
    ),
    (
        "nox_subcommand_on_bridge_cli",
        re.compile(
            r"kol[_-]bridge(?:-cli|_tool\.py)\s+"
            r"(?:quota-snapshot|diligence-pack|nox-audience-check|nox-quota-snapshot|creator-search|doctor)\b",
            re.I,
        ),
        (
            f"Nox subcommands belong on {NOX_TOOL_INVOCATION} — not kol_bridge_tool.py. "
            "Example: nox_kol_tool.py quota-snapshot --env LIVE"
        ),
    ),
    (
        "invalid_plain_on_discovery_skip",
        re.compile(r"list-discovery-skip-handles[^\n]*--plain\b", re.I),
        (
            "list-discovery-skip-handles returns JSON — omit --plain; "
            "parse items[*].handle and items[*].reason."
        ),
    ),
    (
        "invalid_subcommand_find_identity",
        re.compile(r"kol[_-]bridge(?:-cli|_tool\.py)\s+find-identity", re.I),
        "No find-identity-by-handle. Use list-candidate-handles or get-identity --identity-id.",
    ),
    (
        "invalid_cli_pretty_flag_position",
        re.compile(
            r"kol[_-]bridge(?:-cli|_tool\.py)\s+(?!--pretty)[\w-]+[^\n]*\s--pretty\b",
            re.I,
        ),
        (
            "`--pretty` is a global flag before the subcommand: "
            "python3 -u .../kol_bridge_tool.py --pretty get-identity ..."
        ),
    ),
)

_FILE_TOOL_BLOCKED_SUFFIXES = (
    "plugin_api.py",
    "reply_draft.py",
    "serve.py",
    "cal.py",
    "_subcmd_",
)


def lint_agent_bridge_snippet(text: str) -> list[dict[str, str]]:
    """Return structured violations for agent code that bypasses kol_bridge_tool."""
    if not text.strip():
        return []
    hits: list[dict[str, str]] = []
    for code, pattern, hint in _AGENT_LINT_PATTERNS:
        if pattern.search(text):
            hits.append({"code": code, "hint": hint, "canonical_cli": CANONICAL_CLI_REL})
    return hits


def lint_terminal_command(command: str) -> list[dict[str, str]]:
    """Block terminal commands that curl the bridge or embed secrets."""
    if not command.strip():
        return []
    return lint_agent_bridge_snippet(command)


def lint_file_tool_path(path: str) -> list[dict[str, str]]:
    """Block read/search of kol-ops-bridge implementation files."""
    if not path.strip():
        return []
    norm = path.replace("\\", "/").lower()
    if "kol-ops-bridge" not in norm and "kol_ops_bridge" not in norm:
        return []
    hits: list[dict[str, str]] = []
    for suffix in _FILE_TOOL_BLOCKED_SUFFIXES:
        if suffix in norm:
            hits.append({
                "code": "read_bridge_source_file",
                "hint": (
                    "Do not read bridge plugin files. Use kol_bridge_tool.py "
                    "subcommand --help or skill_view(bridge-http-api-endpoints)."
                ),
                "canonical_cli": CANONICAL_CLI_REL,
            })
            break
    if "scripts/kol_bridge_tool" in norm or norm.endswith("kol_bridge_tool.py"):
        hits.append({
            "code": "read_bridge_cli_source",
            "hint": "Run kol_bridge_tool.py --help; do not read the CLI source.",
            "canonical_cli": CANONICAL_CLI_REL,
        })
    return hits


def format_block_message(violations: Iterable[dict[str, str]]) -> str:
    """Single JSON error string for Hermes tool results."""
    import json

    items = list(violations)
    primary = items[0] if items else {"code": "bridge_contract", "hint": "Use kol_bridge_tool.py"}
    payload = {
        "error": "bridge_agent_contract_violation",
        "source": "kol_bridge_agent_guard",
        "note": (
            "Agent guard blocked an unsafe CLI pattern — not bridge HTTP/JSON validation. "
            "Fix the terminal command per hint; do not switch to execute_code."
        ),
        "code": primary.get("code"),
        "hint": primary.get("hint"),
        "canonical_cli": CANONICAL_CLI_REL,
        "violations": items,
    }
    return json.dumps(payload, ensure_ascii=False)


def gateway_contract_block(repo_root: str | None = None) -> str:
    cli = gateway_cli_invocation(repo_root)
    lines = list(AGENT_BRIDGE_CONTRACT_LINES)
    if repo_root:
        lines[1] = f"1. CAL reads/writes: {cli} <subcommand> --env LIVE|TEST ..."
    return "\n".join(lines)


def resume_cli_checklist(
    *,
    escalation_id: int | str,
    identity_id: int | str,
    campaign_id: str,
    env: str,
    require_draft: bool,
    operator_user_id: int | None = None,
    repo_root: str | None = None,
) -> str:
    """Ordered CLI steps for escalation resume runs (paste into gateway brief)."""
    cli = gateway_cli_invocation(repo_root)
    op_line = ""
    if operator_user_id is not None:
        op_line = f"  --operator-user-id {operator_user_id}"
    draft_lines = []
    if require_draft:
        draft_lines = [
            f"{cli} get-email-conversation "
            f"--identity-id {identity_id} --campaign-id {campaign_id} --env {env}{op_line}",
            f"{cli} get-policy --scope company_style",
            f"{cli} persist-reply-draft --env {env} --json @/tmp/draft.json",
            f"{cli} list-approvals --status pending --env {env}",
        ]
    return "\n".join([
        "# bridge_cli_checklist (mandatory — terminal only, no execute_code for bridge)",
        f"{cli} get-escalation --escalation-id {escalation_id} --env {env}",
        dispatch_context_cli_line(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
            repo_root=repo_root,
        ),
        f"{cli} write-facts-multi --identity-id {identity_id} --env {env} "
        "--json @/tmp/resume_facts.json",
        dispatch_context_cli_line(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
            repo_root=repo_root,
        ),
        *draft_lines,
        f"{cli} write-event --env {env} --json @/tmp/resume_event.json",
        "(Write /tmp/resume_event.json with identity_id, campaign_id, "
        'event_type escalation_resume_processed, actor skill:kol-escalation-resumer.)',
        "When require_draft: persist via persist-reply-draft with "
        "linked_escalation_id, conversation_summary.bullets (Chinese), "
        "and summary_only kol-reply-synthesizer when child is not "
        "synthesizer; address resume_context.pending_inbounds "
        "(use latest_pending_inbound_message_id as source_message_id "
        "when set); use operator_answer facts — no stall prose.",
        "Return kol-escalation-resumer JSON envelope (body null unless brief requires draft).",
    ])


def draft_preview_cli_checklist(
    *,
    escalation_id: int | str,
    identity_id: int | str,
    campaign_id: str,
    env: str,
    operator_user_id: int | None = None,
    repo_root: str | None = None,
) -> str:
    """Read-only + draft write steps for escalation preview runs."""
    cli = gateway_cli_invocation(repo_root)
    op_line = ""
    if operator_user_id is not None:
        op_line = f"  --operator-user-id {operator_user_id}"
    return "\n".join([
        "# bridge_cli_checklist (preview — read-only on escalation row)",
        f"{cli} get-escalation --escalation-id {escalation_id} --env {env}",
        dispatch_context_cli_line(
            identity_id=identity_id, campaign_id=campaign_id, env=env,
            repo_root=repo_root,
        ),
        f"{cli} get-email-conversation --identity-id {identity_id} "
        f"--campaign-id {campaign_id} --env {env}{op_line}",
        f"{cli} get-policy --scope company_style",
        f"{cli} persist-reply-draft --env {env} --json @/tmp/draft.json",
        "persist JSON must include top-level conversation_summary.bullets "
        "(Chinese operator recap); use summary_only kol-reply-synthesizer "
        "when drafting via a single child skill.",
        "Do NOT resolve-escalation or PATCH /escalations/{id}.",
    ])


def discovery_cli_rules(repo_root: str | None = None) -> str:
    cli = gateway_cli_invocation(repo_root)
    nox = NOX_TOOL_INVOCATION
    if repo_root:
        from pathlib import Path
        nox = f"{CLI_PYTHON} -u {Path(repo_root).expanduser().resolve() / NOX_TOOL_REL}"
    return "\n".join([
        "# bridge_cli_rules (discovery / rediscover)",
        f"Ingest: {cli} ingest-confirmed-candidate --campaign-id <cid> --env <env> "
        "--json @/tmp/ingest_<handle>.json — one handle per call immediately after qualification.",
        "Ingest JSON requires top-level source + identity + candidate (primary_handle inside identity; "
        "profile URL in identity_facts as identity.instagram_profile_url). NOT flat handle/profile_url/bio.",
        "Do NOT batch multiple handles in execute_code. Do NOT write /tmp/ingest_*.json via execute_code loops.",
        "Do NOT chain ingest-confirmed-candidate with `;`, `&&`, or `||` in one terminal call.",
        "Do NOT use ingest-confirmed-candidate in kol-cold-outreach — identity_id already exists.",
        f"Preflight: {cli} list-outreach-cooldown-handles --env <env> --plain",
        f"{cli} list-discovery-skip-handles --env <env> "
        "(JSON — parse items[*].handle + items[*].reason; do not use --plain)",
        f"Nox (when nox_discovery_enabled): {nox} doctor|quota-snapshot|diligence-pack — "
        "never on kol_bridge_tool.py (quota-snapshot is NOT a bridge subcommand).",
        "Invalid bridge subcommands: read-identity, list-campaigns, find-identity-by-handle, "
        "quota-snapshot, nox-audience-check. Use get-identity / get-campaign / list-candidate-handles.",
        "`--pretty` is global before subcommand. Guard blocks batch execute_code ingest — "
        "not bridge JSON errors.",
    ])


def reply_dispatcher_cli_rules(repo_root: str | None = None) -> str:
    cli = gateway_cli_invocation(repo_root)
    return "\n".join([
        "# bridge_cli_rules (reply-dispatcher)",
        f"Use the terminal tool with {cli} — never execute_code+subprocess for bridge.",
        "Reads: get-dispatch-context --view agent, get-reply-chase-hint, list-events (as needed).",
        "Writes: write-facts-multi, persist-reply-draft (+ conversation_summary), "
        "open-escalation, mark-reply-handled.",
    ])


def terminal_safety_rules(*, repo_root: str | None = None) -> str:
    """Terminal hygiene for gateway runs (avoids AGENTS.md harness injection)."""
    cli = cli_invocation_abs(repo_root) if repo_root else CLI_INVOCATION
    return "\n".join([
        "# terminal_safety (mandatory)",
        f"Bridge CLI: {cli} <subcommand> --env <env> ...",
        (
            "Copy the exact python3 -u .../kol_bridge_tool.py prefix from this brief — "
            "never bare `python`, never relative `plugins/...` from $HOME, "
            "never `python3` on the kol-bridge-cli bash wrapper (it is not Python)."
        ),
        (
            "Every bridge CLI call must print JSON to terminal stdout — never redirect "
            "stdout with `> file` (45-char empty wrapper). Never pipe through "
            "head/grep/jq. `| tee` is OK. `2>/dev/null` alone is OK."
        ),
        (
            "Invalid subcommands (do not use): read-identity → get-identity; "
            "list-campaigns → get-campaign; find-identity-by-handle → list-candidate-handles. "
            "`--pretty` is global: kol_bridge_tool.py --pretty get-identity ..."
        ),
        (
            "Guard block JSON includes source=kol_bridge_agent_guard — that is NOT bridge "
            "validation failure; fix the command per hint."
        ),
        "Do NOT run bare `cd .../hermes-agent` (triggers doc injection, empty stdout).",
        "Do NOT use inline shell JSON for write-event; use `cat > /tmp/event.json` then `--json @/tmp/event.json`.",
        "One subcommand per terminal call; never `python3 -c` + subprocess wrappers.",
        (
            "CLI failures print one JSON line on **stdout** (mirrored to stderr for humans). "
            "Empty terminal output with exit 2 means read stdout for `error`/`hint` — "
            "never abandon the CLI for execute_code."
        ),
    ])


def cold_outreach_thread_anchor(*, campaign_id: str, identity_id: int | str) -> dict[str, str]:
    """Stable anchors for initial (cold) outreach persist — do not randomize per run."""
    return {
        "source_message_id": f"draft:outreach_{campaign_id}_{identity_id}",
        "thread_id": f"outreach_{campaign_id}_{identity_id}",
    }


def _email_discovery_checklist_line(
    *,
    identity_id: int | str,
    email_discovery_queued_ids: set[int] | None,
) -> str:
    """Align checklist with Console approve brief (# email_discovery_queued)."""
    iid = int(identity_id)
    if email_discovery_queued_ids and iid in email_discovery_queued_ids:
        return (
            "If primary_email empty AND this identity is under "
            "# email_discovery_queued: skip draft (pending_email_discovery); "
            "do NOT run browser or kol-email-discovery in this outreach run."
        )
    return (
        "If primary_email empty AND NOT under # email_discovery_queued: "
        "open-escalation contact_email_not_found (Console queues discover separately)."
    )


def _creator_brief_checklist_line(
    *,
    identity_id: int | str,
    creator_brief_queued_ids: set[int] | None,
) -> str:
    """Align checklist with Console approve brief (# creator_brief_queued)."""
    iid = int(identity_id)
    if creator_brief_queued_ids and iid in creator_brief_queued_ids:
        return (
            "If this identity is under # creator_brief_queued: skip draft "
            "(pending_creator_brief); do NOT run browser or "
            "kol-creator-brief-loader in this outreach run."
        )
    return (
        "Before kol-cold-outreach / kol-reengagement-outreach: ensure creator "
        "brief is fresh (6 identity.* keys + content_pillars_discovered_at "
        "within 90 days via get-facts or cal_snapshot). If missing/stale and "
        "NOT under # creator_brief_queued, draft with passive loader only "
        "(browser blocked here) and accept low_personalization."
    )


def redraft_cli_checklist(
    *,
    campaign_id: str,
    env: str,
    identity_id: int | str,
    repo_root: str | None = None,
    email_discovery_queued: bool = False,
) -> str:
    """Write-only CLI steps for single-KOL redraft (reads come from cal_snapshot)."""
    cli = gateway_cli_invocation(repo_root)
    anchors = cold_outreach_thread_anchor(campaign_id=campaign_id, identity_id=identity_id)
    if email_discovery_queued:
        email_line = (
            "If primary_email empty: email discover already queued — skip draft until complete."
        )
    else:
        email_line = (
            "If primary_email empty: open-escalation contact_email_not_found "
            "(do not run kol-email-discovery in this draft session — browser blocked)."
        )
    return "\n".join([
        "# bridge_cli_checklist (redraft — writes only; reads are in cal_snapshot above)",
        (
            "Do NOT run get-campaign / get-identity / get-dispatch-context via terminal "
            "when cal_snapshot is present."
        ),
        email_line,
        (
            "Draft via kol-cold-outreach or kol-reengagement-outreach SKILL; "
            f"write child_envelope to /tmp/outreach_persist_{identity_id}.json wrapper "
            "(see kol-cold-outreach SKILL — not a separate draft-only file)."
        ),
        f"{cli} persist-initial-outreach-draft --env {env} "
        f"--json @/tmp/outreach_persist_{identity_id}.json",
        (
            f"(persist JSON must include child_envelope with subject/body/to; "
            f"anchors auto-set source={anchors['source_message_id']} "
            f"thread={anchors['thread_id']})"
        ),
        "Never write approval.reply_draft via write-facts-multi or HTTP urllib.",
    ])


def approval_cli_checklist(
    *,
    campaign_id: str,
    env: str,
    identity_ids: list[int] | list[str],
    repo_root: str | None = None,
    email_discovery_queued_ids: list[int] | None = None,
    creator_brief_queued_ids: list[int] | None = None,
) -> str:
    """Ordered CLI steps for post-shortlist approval / outreach runs."""
    cli = gateway_cli_invocation(repo_root)
    queued = {int(i) for i in (email_discovery_queued_ids or [])}
    brief_queued = {int(i) for i in (creator_brief_queued_ids or [])}
    per_kol = []
    for iid in identity_ids:
        anchors = cold_outreach_thread_anchor(campaign_id=campaign_id, identity_id=iid)
        per_kol.extend([
            f"# identity {iid}",
            f"{cli} write-event --env {env} --json @/tmp/event_{iid}.json",
            "(event JSON: identity_id, campaign_id, event_type shortlist_approval_received, actor from brief)",
            f"{cli} get-identity --identity-id {iid} --env {env}",
            dispatch_context_cli_line(
                identity_id=iid, campaign_id=campaign_id, env=env,
                repo_root=repo_root,
            ),
            _email_discovery_checklist_line(
                identity_id=iid,
                email_discovery_queued_ids=queued,
            ),
            _creator_brief_checklist_line(
                identity_id=iid,
                creator_brief_queued_ids=brief_queued,
            ),
            (
                "Draft via kol-cold-outreach or kol-reengagement-outreach SKILL; "
                f"write persist wrapper /tmp/outreach_persist_{iid}.json "
                "(child_envelope inside — see kol-cold-outreach SKILL)."
            ),
            f"{cli} persist-initial-outreach-draft --env {env} "
            f"--json @/tmp/outreach_persist_{iid}.json",
            (
                f"(persist JSON must include child_envelope with subject/body/to; "
                f"anchors auto-set source={anchors['source_message_id']} "
                f"thread={anchors['thread_id']})"
            ),
        ])
    return "\n".join([
        "# bridge_cli_checklist (post-approval outreach — terminal only)",
        f"{cli} get-campaign --campaign-id {campaign_id} --env {env}",
        *per_kol,
        f"{cli} list-approvals --status pending --env {env}",
        "Never write approval.reply_draft via write-facts-multi or HTTP urllib.",
    ])
