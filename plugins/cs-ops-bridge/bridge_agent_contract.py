"""Agent contract strings for cs-ops-bridge gateway briefs."""

from __future__ import annotations

from pathlib import Path

from .profile_refs import quickcep_skill_dir, hindsight_bridge_script, hindsight_bank_id, hindsight_recall_tracker_script


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


def _tool_rules_block(*, resume: bool = False) -> str:
    """Shared gateway tool rules for process/resume briefs."""
    hindsight = (
        "- **Hindsight step (step 5) MUST use the terminal tool**, NOT execute_code. "
        "If the bridge script times out, retry via terminal with the direct API curl command — never via execute_code.\n"
        if resume
        else ""
    )
    esc17 = " execute_code scripts lose error context, cannot handle timeouts properly, and have caused Hindsight retain failures (ESC:17)." if resume else ""
    return f"""# tool_rules (CRITICAL — violation causes silent data loss)
- **The `terminal` tool IS in your available tool list** — call it directly for every bridge CLI step. Do NOT claim terminal is missing.
- **EVERY step below MUST be executed via the terminal tool.** One command per terminal call.
- **delegate_task is PROHIBITED** for cs_bridge_tool or shell commands — it drops output and breaks the checklist.
- **execute_code is STRICTLY PROHIBITED.** Do NOT use execute_code, subprocess.run, os.system, or any Python sandbox to run bridge CLI commands.{esc17}
{hindsight}- Do **NOT** call quickcep_cli directly.
"""


def process_cli_checklist(*, env: str, quickcep_session_id: str) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    tracker_script = hindsight_recall_tracker_script()
    draft_path = f"/tmp/draft-{quickcep_session_id}.html"
    return f"""# agent_tool_paths
cs_bridge_tool: {paths['cs_bridge_tool']}

{_tool_rules_block()}

# bridge_cli_checklist
0. skill_view(name='povison-cs-orchestrator-flow')
1. terminal: python3 {cli} get-dispatch-context --env {env} --session-id {quickcep_session_id}
   dispatch-context returns orders and may include tracking prefill when intentionTags contain 物流咨询 and orders are present.
2. terminal: python3 {cli} get-messages --env {env} --session-id {quickcep_session_id}
3. terminal: python3 {cli} classify-intent --env {env} --subject "<subject>" --body "<body>"
4. terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase processing --customer-need "<中文：客户诉求摘要>" --classify-json '<classify JSON>'
5. (auto_handle) product/logistics lookup skills → terminal: python3 {cli} draft-save --env {env} --session-id {quickcep_session_id} --content-file {draft_path} --subject "Re: <subject>" --receiver "<customer email>"
   draft-save default writes to CAL (no joinChat). Legacy QuickCEP path (--legacy-quickcep-draft) auto-calls join-chat before save. joinChat is also called by the watcher at launch time (fail-soft) so the AI account is already visible in QuickCEP. If using --content inline in shell, wrap in **single quotes** when text contains $ (e.g. --content 'refund $200 after delivery'). Double quotes mangle $200 → 00.
   **Internal domain guard**: draft-save automatically blocks drafts containing internal/backend URLs (OSS buckets, localhost, feishu.cn, internal IPs/ports). If blocked, strip internal links and retry. Never put internal system URLs in customer-facing drafts.
6. (auto_handle) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase draft_ready --actions-taken "已查询并生成回复草稿" --operator-hint "<中文：操作员接手要点>"
6.5. (escalate candidate) **Check Hindsight** before escalating — call hindsight_recall(query="<product slug> <problem keywords>"), then IMMEDIATELY record to tracker:
   terminal: python3 {tracker_script} record --session-id {quickcep_session_id} --query "<recall query>" --result hit|miss|error --outcome auto_handled|escalated --reason "<if escalated, why>" --product-slug "<slug>" --category "<category>" --num-results <N>
   See §Hindsight Knowledge Recall in povison-cs-orchestrator-flow skill for full procedure. If Hindsight hit → auto_handle (skip to step 5/6). If miss/error → proceed to step 7.
7. (escalate) terminal: python3 {cli} open-escalation --env {env} --session-id {quickcep_session_id} --customer-email "<from get-messages>" --email-summary "<简体中文：客户诉求摘要>" --email-quote "<customer's full original email text>" --reason "<why>" --urgency medium|high|low --question "<expert question>"
   Bridge auto-adds **📦 订单信息** to the Feishu post from QuickCEP orders + tracking (no extra flag). If QuickCEP has no linked orders, the section notes that — include order numbers in --email-summary when known from the email.
   --email-quote carries the customer's complete email body (not a partial excerpt). Multiline or $ in quote → --email-quote-file /tmp/quote.txt. Bridge auto-posts to Feishu AI客服后援; use returned feishu.thread_id in step 8.
8. (escalate) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase awaiting_expert --feishu-thread-id "<thread from open-escalation response>"
9. (failure) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase failed --error "<中文：面向客服的失败说明，勿写 CLI/系统日志>" --customer-need "<中文：客户诉求摘要>" --actions-taken "<中文：已尝试的业务动作，如「已查询订单但未能保存草稿」>"
10. terminal: python3 {cli} update-session-status --env {env} --session-id {quickcep_session_id} --status draft_ready|awaiting_expert|failed
"""


def resume_cli_checklist(*, env: str, escalation_id: int) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    hindsight_cli = hindsight_bridge_script()
    hindsight_bank = hindsight_bank_id()
    return f"""# agent_tool_paths
cs_bridge_tool: {paths['cs_bridge_tool']}

{_tool_rules_block(resume=True)}- quickcep_session_id is the long numeric QuickCEP id — never use CAL internal session id.

# bridge_cli_checklist
0. skill_view(name='povison-cs-escalation-resumer')
1. terminal: python3 {cli} get-escalation --env {env} --escalation-id {escalation_id}
2. terminal: python3 {cli} get-dispatch-context --env {env} --session-id <quickcep_session_id from escalation.session>
3. terminal: python3 {cli} get-messages --env {env} --session-id <quickcep_session_id>
4. terminal: write_file(path='/tmp/draft-<quickcep_session_id>.html', content='<English reply merging operator_answer, Povison tone>')
5. **[REQUIRED] Record Q&A to Hindsight** BEFORE draft-save. This step is NOT optional — skipping it means future identical questions will be re-escalated:
   terminal: python3 {hindsight_cli} retain --bank {hindsight_bank} --content '<structured Q&A per povison-cs-escalation-resumer skill template>' --tags "povison,escalation-qa,<product_slug>,<category>" --context "povison escalation Q&A"
   If bridge script times out (60s), retry via terminal with: curl -s -X POST http://localhost:8888/v1/default/banks/{hindsight_bank}/memories -H 'Content-Type: application/json' -d '{{"content":"<Q&A>","context":"povison escalation Q&A","tags":["povison","escalation-qa"]}}' --max-time 120
   Only skip if Hindsight server is confirmed down.
6. terminal: python3 {cli} draft-save --env {env} --session-id <quickcep_session_id> --content-file /tmp/draft-<quickcep_session_id>.html --subject "Re: ..." --receiver "<email>" (never send-email; join-chat is automatic)
   If get-escalation resume_context includes operator_attachments, pass them via --attachments with the JSON array (fileName, fileSize, url). PDF attachments must be vault-sourced — draft-save attachment guard blocks assembly/static.povison PDFs.
   **Internal domain guard**: draft-save blocks drafts with internal URLs (OSS buckets, localhost, feishu.cn, internal IPs/ports). If blocked, strip internal links and retry.
7. terminal: python3 {cli} apply-handoff --env {env} --session-id <quickcep_session_id> --phase draft_ready --actions-taken "已合并飞书专家答复并生成草稿" --operator-hint "<中文：操作员接手要点>"
8. terminal: python3 {cli} write-event --env {env} --session-id <quickcep_session_id> --event-type escalation_resumed --json '{{...}}'
9. terminal: python3 {cli} update-session-status --env {env} --session-id <quickcep_session_id> --status draft_ready
"""


def process_instructions() -> str:
    paths = agent_tool_paths()
    return (
        "You are running the povison-cs-orchestrator-flow skill for an inbound QuickCEP **email** session only. "
        "Ignore web chat, SMS, phone, and other channels — automation is scoped to email. "
        f"Use ONLY cs_bridge_tool at {paths['cs_bridge_tool']} for all bridge and QuickCEP operations "
        "(get-messages, draft-save, apply-handoff, classify-intent, open-escalation, etc.). "
        "The **terminal** tool is available in your tool list — invoke each cs_bridge_tool subcommand "
        "via terminal directly (one command per call). "
        "Do NOT use delegate_task to run shell commands or cs_bridge_tool. "
        "execute_code is STRICTLY PROHIBITED for ANY step in this workflow. "
        "Do NOT use execute_code, subprocess.run, os.system, or any Python sandbox. "
        "Do not call quickcep_cli directly — it is not part of the agent contract. "
        "Do not search for or guess script locations. "
        "NEVER send-email to customers; only draft-save with --content or --content-file, plus --subject --receiver. "
        "Never use shared temp files like /tmp/draft.html; always use session-scoped paths (/tmp/draft-<quickcep_session_id>.html). "
        "Never include internal/backend URLs in customer drafts — draft-save has an automatic guard that blocks "
        "internal domains (OSS buckets, localhost, feishu.cn, internal IPs/ports). If the guard blocks your draft, "
        "strip the internal links and retry. "
        "Shell double quotes expand $200 as positional $2 + '00' → corrupts dollar amounts; use single quotes or --content-file. "
        "Escalate via cs_bridge_tool open-escalation (bridge posts to Feishu automatically). "
        "open-escalation MUST include --customer-email, --email-summary (Simplified Chinese), and --email-quote "
        "(the customer's full original email text): after reading get-messages, "
        "summarize in Chinese and paste the customer's complete email body into --email-quote. "
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
        "The **terminal** tool is available in your tool list — invoke each subcommand via terminal "
        "directly (one command per call). "
        "Do NOT use delegate_task to run shell commands or cs_bridge_tool. "
        "execute_code is STRICTLY PROHIBITED for ANY step in this workflow. "
        "Do NOT use execute_code, subprocess.run, os.system, or any Python sandbox — "
        "this has caused Hindsight retain failures (ESC:17) and broken draft saves. "
        "Every bridge CLI command and every Hindsight command MUST go through the terminal tool. "
        "Do not call quickcep_cli directly. "
        "First step: skill_view(name='povison-cs-escalation-resumer'). "
        "Load escalation context via cs_bridge_tool, incorporate operator_answer into the "
        "customer reply, and write a QuickCEP draft for human review. "
        "MANDATORY: After merging operator_answer and BEFORE draft-save, record the Q&A pair to "
        "Hindsight memory via hindsight_bridge.py retain (see step 5 in the checklist) — "
        "executed via the **terminal** tool, NOT execute_code. "
        "If hindsight_bridge.py times out, retry via terminal with a curl command (see checklist). "
        "This enables future auto-handling of identical questions without re-escalation. "
        "When resume_context includes operator_attachments, include them in draft-save --attachments. "
        "Only vault-uploaded PDFs may be attached — product assembly PDFs must be text in the body."
    )


def edit_memory_instructions() -> str:
    return (
        "You are running the operator-edit-memory step. The operator has reviewed and edited the "
        "AI-generated reply draft, then sent it to the customer. Your ONLY job is to analyze what "
        "the operator changed versus the original AI draft and record any **product/policy information** "
        "corrections into Hindsight memory so future replies reflect the operator's factual fix.\n\n"
        "You inherit the full reply-generation context (this conversation). Compare the AI draft against "
        "the operator-edited draft provided in the input. Focus ONLY on factual corrections about "
        "products, policies, specs, pricing, shipping, returns, or warranty — ignore tone/style edits, "
        "greetings, typos, and formatting changes.\n\n"
        "STRICT TOOL CONSTRAINT: this run is guard-locked to hindsight memory tools only. You may call "
        "hindsight_recall to check what's already known, hindsight_retain to persist each factual "
        "correction as a distinct memory, and hindsight_reflect to consolidate. You MUST NOT call any "
        "other tool — no terminal, no execute_code, no cs_bridge_tool, no send_message. If no factual "
        "product/policy change exists, retain nothing and finish.\n\n"
        "When retaining, write concise, self-contained facts (e.g. \"产品X的承重为150kg，非200kg\"), "
        "tagged with the product/SKU context when available. Never retain PII or customer-specific details."
    )
