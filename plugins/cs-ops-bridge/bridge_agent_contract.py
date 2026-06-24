"""Agent contract strings for cs-ops-bridge gateway briefs."""

from __future__ import annotations

from pathlib import Path

from .profile_refs import quickcep_skill_dir


def cs_bridge_cli_path() -> Path:
    return Path(__file__).resolve().parent / "scripts" / "cs_bridge_tool.py"


def quickcep_cli_path() -> Path:
    """Internal QuickCEP CLI path (bridge/watcher only — not exposed to agents)."""
    return quickcep_skill_dir() / "scripts" / "quickcep_cli.py"


def agent_tool_paths() -> dict[str, str]:
    """Canonical absolute paths for agent terminal commands (do not guess)."""
    return {
        "cs_bridge_tool": str(cs_bridge_cli_path()),
        "bridge_env": "HERMES_CS_OPS_BRIDGE_KEY and CS_OPS_BRIDGE_BASE must be set (profile .env)",
    }


def process_cli_checklist(*, env: str, quickcep_session_id: str) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    return f"""# agent_tool_paths
cs_bridge_tool: {paths['cs_bridge_tool']}

# bridge_cli_checklist
1. python3 {cli} get-dispatch-context --env {env} --session-id {quickcep_session_id}
2. python3 {cli} get-messages --env {env} --session-id {quickcep_session_id}
3. python3 {cli} classify-intent --env {env} --subject "<subject>" --body "<body>"
4. python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase processing --customer-need "<中文：客户诉求摘要>" --classify-json '<classify JSON>'
5. (auto_handle) product/logistics lookup skills → python3 {cli} draft-save --env {env} --session-id {quickcep_session_id} --content-file /tmp/draft.html --subject "Re: <subject>" --receiver "<customer email>"
   draft-save auto-calls join-chat before QuickCEP save. If using --content inline in shell, wrap in **single quotes** when text contains $ (e.g. --content 'refund $200 after delivery'). Double quotes mangle $200 → 00.
6. (auto_handle) python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase draft_ready --actions-taken "已查询并保存回复草稿" --operator-hint "<中文：操作员接手要点>"
7. (escalate) python3 {cli} open-escalation --env {env} --session-id {quickcep_session_id} --customer-email "<from get-messages>" --email-summary "<简体中文：客户诉求摘要>" --email-quote "<partial quote in customer's original language>" --reason "<why>" --urgency medium|high|low --question "<expert question>"
   Multiline or $ in quote → --email-quote-file /tmp/quote.txt. Bridge auto-posts to Feishu AI客服后援; use returned feishu.thread_id in step 8.
8. (escalate) python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase awaiting_expert --feishu-thread-id "<thread from open-escalation response>"
9. (failure) python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase failed --error "<中文：面向客服的失败说明，勿写 CLI/系统日志>" --customer-need "<中文：客户诉求摘要>" --actions-taken "<中文：已尝试的业务动作，如「已查询订单但未能保存草稿」>"
10. python3 {cli} update-session-status --env {env} --session-id {quickcep_session_id} --status draft_ready|awaiting_expert|failed
"""


def resume_cli_checklist(*, env: str, escalation_id: int) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    return f"""# agent_tool_paths
cs_bridge_tool: {paths['cs_bridge_tool']}

# bridge_cli_checklist
1. python3 {cli} get-escalation --env {env} --escalation-id {escalation_id}
2. python3 {cli} get-dispatch-context --env {env} --session-id <from escalation.session>
3. python3 {cli} get-messages --env {env} --session-id <session_id>
4. Merge operator_answer into customer reply (English, Povison tone) → write to /tmp/draft.html
5. **[REQUIRED] Record Q&A to Hindsight** BEFORE draft-save. This step is NOT optional — skipping it means future identical questions will be re-escalated:
   python3 /Users/arnold/.hermes/skills/hindsight-memory/scripts/hindsight_bridge.py retain --bank povison-cs-hermes-knowledge --content '<structured Q&A per povison-cs-escalation-resumer skill template>' --tags "povison,escalation-qa,<product_slug>,<category>" --context "povison escalation Q&A"
   If bridge script times out (60s), retry with direct API call using 120s timeout (see skill §Timeout handling). Only skip if Hindsight server is confirmed down.
6. python3 {cli} draft-save --env {env} --session-id <id> --content-file /tmp/draft.html --subject "Re: ..." --receiver "<email>" (never send-email; join-chat is automatic)
7. python3 {cli} apply-handoff --env {env} --session-id <id> --phase draft_ready --actions-taken "已合并飞书专家答复并保存草稿" --operator-hint "<中文：操作员接手要点>"
8. python3 {cli} write-event --env {env} --session-id <id> --event-type escalation_resumed --json '{{...}}'
9. python3 {cli} update-session-status --env {env} --session-id <id> --status draft_ready
"""


def process_instructions() -> str:
    paths = agent_tool_paths()
    return (
        "You are running the povison-cs-orchestrator-flow skill for an inbound QuickCEP **email** session only. "
        "Ignore web chat, SMS, phone, and other channels — automation is scoped to email. "
        f"Use ONLY cs_bridge_tool at {paths['cs_bridge_tool']} for all bridge and QuickCEP operations "
        "(get-messages, draft-save, apply-handoff, classify-intent, open-escalation, etc.). "
        "Do not call quickcep_cli directly — it is not part of the agent contract. "
        "Do not search for or guess script locations. "
        "NEVER send-email to customers; only draft-save with --content or --content-file, plus --subject --receiver. "
        "Shell double quotes expand $200 as positional $2 + '00' → corrupts dollar amounts; use single quotes or --content-file. "
        "Escalate via cs_bridge_tool open-escalation (bridge posts to Feishu automatically). "
        "open-escalation MUST include --customer-email, --email-summary (Simplified Chinese), and --email-quote "
        "(partial quote in the customer's original language): after reading get-messages, "
        "summarize in Chinese and quote 1–3 relevant sentences in the original language (never paste the full thread). "
        "Do NOT use send_message for Feishu during api_server CS runs. "
        "All apply-handoff note fields (--customer-need, --actions-taken, --follow-up, --operator-hint, --error) "
        "MUST be Simplified Chinese written for customer-service operators — session business only "
        "(what the customer wants, what was tried, what the operator should do next). "
        "Never mention CLI flags, gateway/bridge, logs, run_id, message_id, or other engineering details. "
        "Internal QuickCEP notes are operator-facing Chinese only. "
        "apply-handoff --phase must be one of: processing, draft_ready, awaiting_expert, failed, "
        "reviewed, followup_while_busy, operator_sent — never invent phase names."
    )


def resume_instructions() -> str:
    paths = agent_tool_paths()
    return (
        "You are running povison-cs-escalation-resumer after an operator answered in Feishu. "
        f"Use ONLY cs_bridge_tool at {paths['cs_bridge_tool']} (get-escalation, get-messages, draft-save, apply-handoff). "
        "Do not call quickcep_cli directly. "
        "Load escalation context via cs_bridge_tool, incorporate operator_answer into the "
        "customer reply, and write a QuickCEP draft for human review. "
        "MANDATORY: After merging operator_answer and BEFORE draft-save, record the Q&A pair to "
        "Hindsight memory via hindsight_bridge.py retain (see step 5 in the checklist). "
        "This enables future auto-handling of identical questions without re-escalation."
    )
