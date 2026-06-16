"""Shared bridge agent contract — lint rules + gateway brief snippets.

Used by ``scripts/_cli_guardrails.py``, Console gateway instructions, and the
``kol-bridge-agent-guard`` Hermes plugin hook.
"""

from __future__ import annotations

import re
from typing import Iterable

CANONICAL_CLI_REL = "plugins/kol-ops-bridge/scripts/kol_bridge_tool.py"
CLI_WRAPPER_REL = "plugins/kol-ops-bridge/scripts/kol-bridge-cli"
CLI_PYTHON = "python3"
CLI_INVOCATION = f"{CLI_PYTHON} {CANONICAL_CLI_REL}"
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
            r"/tmp/ingest_[^'\"\s]+\.json|"
            r"candidates\s*=\s*\[[^\]]+\][^\n]*ingest",
            re.I | re.S,
        ),
        "Ingest one handle at a time: write @/tmp/ingest_<handle>.json then ingest-confirmed-candidate immediately.",
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
        "python3_on_kol_bridge_cli_wrapper",
        re.compile(
            r"python3(?:\s+-u)?\s+[^\s;|&]*kol-bridge-cli\b",
            re.I,
        ),
        "kol-bridge-cli is a bash script — run python3 -u .../kol_bridge_tool.py directly "
        "(exact prefix is in the gateway brief).",
    ),
    (
        "redirect_bridge_read_stdout",
        re.compile(
            r"kol[_-]bridge(?:-cli|_tool\.py)\s+get-(?:campaign|identity|dispatch-context|"
            r"facts|escalation|email-conversation)[^\n]*>\s*",
            re.I,
        ),
        "Read subcommands must print JSON to terminal stdout — do not redirect to a file.",
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
    return "\n".join([
        "# bridge_cli_rules (discovery / rediscover)",
        f"Ingest: {cli} ingest-confirmed-candidate --campaign-id <cid> --env <env> "
        "--json @/tmp/ingest_<handle>.json — one handle per call immediately after qualification.",
        "Ingest JSON requires top-level source + identity + candidate (primary_handle inside identity; "
        "profile URL in identity_facts as identity.instagram_profile_url). NOT flat handle/profile_url/bio.",
        "Do NOT batch multiple handles in execute_code. Do NOT write /tmp/ingest_*.json via execute_code loops.",
        "Do NOT use ingest-confirmed-candidate in kol-cold-outreach — identity_id already exists.",
        f"Preflight: {cli} list-outreach-cooldown-handles --env <env> --plain",
        f"{cli} list-discovery-skip-handles --env <env> "
        "(JSON — parse items[*].handle + items[*].reason; do not use --plain)",
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
            "Read subcommands (get-campaign, get-identity, get-dispatch-context, get-facts, "
            "get-escalation) must print JSON directly to terminal stdout — "
            "do NOT redirect with `>` (empty stdout looks like CAL failure)."
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


def redraft_cli_checklist(
    *,
    campaign_id: str,
    env: str,
    identity_id: int | str,
    repo_root: str | None = None,
) -> str:
    """Write-only CLI steps for single-KOL redraft (reads come from cal_snapshot)."""
    cli = gateway_cli_invocation(repo_root)
    anchors = cold_outreach_thread_anchor(campaign_id=campaign_id, identity_id=identity_id)
    return "\n".join([
        "# bridge_cli_checklist (redraft — writes only; reads are in cal_snapshot above)",
        (
            "Do NOT run get-campaign / get-identity / get-dispatch-context via terminal "
            "when cal_snapshot is present."
        ),
        "If primary_email empty: delegate kol-email-discovery; on miss open-escalation contact_email_not_found.",
        (
            "Draft via kol-cold-outreach or kol-reengagement-outreach SKILL; "
            f"write /tmp/outreach_{identity_id}.json."
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
) -> str:
    """Ordered CLI steps for post-shortlist approval / outreach runs."""
    cli = gateway_cli_invocation(repo_root)
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
            "If primary_email empty: delegate kol-email-discovery; on miss open-escalation contact_email_not_found.",
            "Draft via kol-cold-outreach or kol-reengagement-outreach SKILL; write /tmp/outreach_{iid}.json.",
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
