"""Agent contract strings for cs-ops-bridge gateway briefs."""

from __future__ import annotations

from pathlib import Path

from .profile_refs import quickcep_skill_dir, hindsight_recall_tracker_script, hindsight_knowledge_bank_id, hindsight_knowledge_base_url


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
        "- **Hindsight knowledge step (step 5) calls the `knowledge_retain` MCP tool** (cs-hindsight-knowledge server), NOT execute_code and NOT the legacy hindsight_bridge.py retain. "
        "If the MCP server is unavailable, retry via terminal with the direct API curl command in the checklist (items + 300s) — never via execute_code.\n"
        if resume
        else ""
    )
    esc17 = " execute_code scripts lose error context, cannot handle timeouts properly, and have caused Hindsight retain failures (ESC:17)." if resume else ""
    return f"""# tool_rules (CRITICAL — violation causes silent data loss)
- **The `terminal` tool IS in your available tool list** — call it directly for every bridge CLI step. Do NOT claim terminal is missing.
- **EVERY cs_bridge_tool step MUST be executed via the terminal tool.** One command per terminal call.
- **Hindsight knowledge tools (`knowledge_retain`, `knowledge_recall`) are MCP tools** — call them directly as tools (not via terminal/execute_code).
- **delegate_task is PROHIBITED** for cs_bridge_tool or shell commands — it drops output and breaks the checklist.
- **execute_code is STRICTLY PROHIBITED.** Do NOT use execute_code, subprocess.run, os.system, or any Python sandbox to run bridge CLI commands.{esc17}
{hindsight}- Do **NOT** call quickcep_cli directly.
"""


def process_cli_checklist(*, env: str, quickcep_session_id: str) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    tracker_script = hindsight_recall_tracker_script()
    draft_path = f"/tmp/draft-{quickcep_session_id}.html"
    gate_block = _gate_extract_block(env=env, quickcep_session_id=quickcep_session_id)
    return f"""{gate_block}# agent_tool_paths
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
   **CRITICAL — NO COMPENSATION IN DRAFTS:** The draft must NOT contain any compensation, goodwill discount, partial refund, or monetary concession of ANY amount — regardless of what Hindsight recalls or what the situation warrants. A code-level guard (`compensation_guard.py`) automatically blocks any draft containing compensation-specific language patterns; the save will abort and you must remove the compensation content before retrying. If the case involves a delay, damage, or service failure where compensation might be appropriate, draft the non-compensation parts (apology, status, tracking, explanation) and escalate via open-escalation (step 7) so the operator decides whether to add compensation. The draft may say "our team will review your case" but must NOT promise any specific remedy or dollar amount.
   draft-save default writes to CAL (no joinChat). Legacy QuickCEP path (--legacy-quickcep-draft) auto-calls join-chat before save. joinChat is also called by the watcher at launch time (fail-soft) so the AI account is already visible in QuickCEP. If using --content inline in shell, wrap in **single quotes** when text contains $ (e.g. --content 'refund $200 after delivery'). Double quotes mangle $200 → 00.
   **Internal domain guard**: draft-save automatically blocks drafts containing internal/backend URLs (OSS buckets, localhost, feishu.cn, internal IPs/ports). If blocked, strip internal links and retry. Never put internal system URLs in customer-facing drafts.
6. (auto_handle) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase draft_ready --actions-taken "已查询并生成回复草稿" --operator-hint "<中文：操作员接手要点>"
6.5. (escalate candidate) **Check Hindsight knowledge** before escalating — call the `knowledge_recall` MCP tool: knowledge_recall(question="<customer question or product slug + problem keywords>", sku="<SKU if known>", product_name="<optional>"). Do NOT use the legacy hindsight_recall tool. For policy questions with no SKU, still call knowledge_recall (the Parser routes domain=policy) — never skip just because there is no SKU.
   The tool returns `parsed` (domain/product_id/attribute/policy_type/applies_to), `request` (the actual query + metadata_filter + tags), and `results`. Then IMMEDIATELY record to tracker:
   terminal: python3 {tracker_script} record --session-id {quickcep_session_id} --query "<request.query from tool result>" --result hit|miss|error --outcome auto_handled|escalated --reason "<if escalated, why>" --product-slug "<slug>" --category "<category>" --num-results <N> --metadata-filter "<JSON of request.metadata_filter>" --domain "<parsed.domain>"
   See §Hindsight Knowledge Recall in povison-cs-orchestrator-flow skill for full procedure. If knowledge_recall hit → auto_handle (skip to step 5/6). If miss/error → proceed to step 7.
7. (escalate) terminal: python3 {cli} open-escalation --env {env} --session-id {quickcep_session_id} --customer-email "<from get-messages>" --email-summary "<简体中文：客户诉求摘要>" --email-quote "<customer's full original email text>" --reason "<why>" --urgency medium|high|low --question "<expert question>"
   Bridge auto-adds **📦 订单信息** to the Feishu post from QuickCEP orders + tracking (no extra flag). If QuickCEP has no linked orders, the section notes that — include order numbers in --email-summary when known from the email.
   --email-quote carries the customer's complete email body (not a partial excerpt). Multiline or $ in quote → --email-quote-file /tmp/quote.txt. Bridge auto-posts to Feishu AI客服后援; use returned feishu.thread_id in step 8.
8. (escalate) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase awaiting_expert --feishu-thread-id "<thread from open-escalation response>"
9. (intentional skip — B2B spam, carrier COI misroute, SEO pitch, out-of-scope) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase skipped --customer-need "<中文：来信性质>" --actions-taken "<中文：为何跳过，如「承运商 COI 误入，无客户订单」>" --follow-up "无需回复客户；建议关闭工单或拉黑发件域名"
   **NEVER** use --phase failed for intentional skips — failed is ONLY for real processing errors (API crash, draft-save failure, etc.). Bridge tags skipped as **AI-已结案**, not AI-处理失败.
9b. (unrecoverable error) terminal: python3 {cli} apply-handoff --env {env} --session-id {quickcep_session_id} --phase failed --error "<中文：面向客服的失败说明，勿写 CLI/系统日志>" --customer-need "<中文：客户诉求摘要>" --actions-taken "<中文：已尝试的业务动作，如「已查询订单但未能保存草稿」>"
10. terminal: python3 {cli} update-session-status --env {env} --session-id {quickcep_session_id} --status draft_ready|awaiting_expert|failed|skipped
"""


def _gate_extract_block(*, env: str, quickcep_session_id: str) -> str:
    """Build the `# gate_extract` brief block when CS_INTENT_ENABLED.

    Fetches the latest gate_extract from the cs-intent-classifier service and
    renders a human-readable markdown block with the agent behavior constraints
    (no re-classify, in_scope handling, no-fabrication, uncertain-field
    confirmation). Returns "" when the switch is off or the classifier is
    unreachable — preserving the legacy brief unchanged.
    """
    import os

    if os.environ.get("CS_INTENT_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return ""
    ge = _fetch_gate_extract(env=env, quickcep_session_id=quickcep_session_id)
    if not ge:
        return ""
    return _render_gate_extract_brief(ge) + "\n"


def _fetch_gate_extract(*, env: str, quickcep_session_id: str) -> dict | None:
    """GET /gate-extract/{id} on the classifier. None on 404/unreachable."""
    import json
    import os
    import urllib.error
    import urllib.request

    base = os.environ.get("CS_INTENT_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
    url = f"{base}/gate-extract/{quickcep_session_id}?env={env}"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except Exception:
        return None


def _render_gate_extract_brief(ge: dict) -> str:
    """Render a gate_extract dict as the agent-facing markdown block."""
    intents = ge.get("intents") or []
    is_closing = bool(ge.get("is_conversation_closing"))
    intent_lines = []
    for it in intents:
        scope_tag = "in_scope" if it.get("in_scope") else "out_of_scope, 转人工/escalate"
        orders = it.get("related_orders") or []
        prods = it.get("related_products") or []
        prod_str = ",".join(p.get("slug") or p.get("name") or "?" for p in prods) if prods else ""
        snippet = (it.get("snippet") or "").replace("\n", " ").strip()
        line = f"- [{scope_tag}] {it.get('intent','?')} — confidence={it.get('confidence','?')}, urgency={it.get('urgency','?')}"
        if orders:
            line += f", orders=[{','.join('#'+o for o in orders)}]"
        if prod_str:
            line += f", products=[{prod_str}]"
        if snippet:
            line += f"\n  > \"{snippet[:200]}\""
        intent_lines.append(line)
    intents_block = "\n".join(intent_lines) or "(none)"
    region = ge.get("customer_region") or {}
    region_line = f"country: {region.get('country') or 'unknown'} | province_state: {region.get('province_state') or 'unknown'} | source: {region.get('source','unknown')} | confidence: {region.get('confidence','low')}"
    uncertain = ge.get("uncertain_fields") or []
    null_fields = ge.get("null_fields") or []
    uncertain_block = "\n".join(f"- {f}" for f in uncertain) if uncertain else "- (none)"
    null_block = "\n".join(f"- {f}" for f in null_fields) if null_fields else "- (none)"
    emotion = ge.get("emotion") or {}
    language = ge.get("language") or {}
    return f"""# gate_extract (pre-classified by cs-intent-classifier {ge.get('model_version','?')}, source={ge.get('classifier_source','?')})
Use these signals. Do NOT re-run classify-intent (step 3) for this session — skip it. Handle in_scope items; for out_of_scope items, tell the customer in the draft that human colleagues will follow up, AND escalate.
NEVER fabricate: fields listed in uncertain_fields / null_fields are unverified — verify via dispatch-context/get-messages, or ask the customer before acting on them.

## Intents
{intents_block}

primary_intent: {ge.get('primary_intent','?')}
in_scope: {str(ge.get('in_scope')).lower()} (any in_scope → handle; out_of_scope parts → escalate/note)
{f'''
## ⚑ Conversation closing
is_conversation_closing: true — 这是一封话题结束邮件（客户纯感谢/确认，无新问题）。
请发送简短的感谢确认回复（如 "You're very welcome! Feel free to reach out if you need anything else."），然后关闭/标记会话为已解决。
不要重新提问、不要尝试解决新问题、不要 escalate。''' if is_closing else ''}

## Customer & context
- emotion: {emotion.get('value','neutral')} (confidence={emotion.get('confidence','medium')}) → 语气适配
- language: {language.get('value','en')} (confidence={language.get('confidence',0.95)}) → 回复必须用此语言
- urgency: {ge.get('urgency','medium')}
- customer_segment: {ge.get('customer_segment','unknown')}
- conversation_stage: {ge.get('conversation_stage','unknown')}
- response_template_hint: {ge.get('response_template_hint') or 'general'}

## Customer region
- {region_line}
  (if unknown → 询问客户或查 dispatch-context 订单地址；勿假设)

## Pre-extracted entities
- orders: {ge.get('orders') or []} → 已在 dispatch-context 提供，直接用
- products: {[p.get('slug') or p.get('name') for p in (ge.get('products') or [])]} → 商品查询用此 slug
- hindsight_keywords: {ge.get('hindsight_keywords') or []} → step 6.5 knowledge_recall 可把这些关键词拼进 question 辅助 Parser

## Summary (中文，客户诉求概括)
{ge.get('summary_zh') or '(none)'}

## Uncertain fields (verify before acting — do NOT treat as fact)
{uncertain_block}

## Null fields (no reliable signal — ask customer if needed)
{null_block}

## Fabrication guard
- {('passed: all values sourced from email content or metadata; nulls marked above' if ge.get('fabrication_guard') else 'FAILED — do not trust this gate_extract, fall back to classify-intent step 3')}

## Notes
- pii_flag: {str(ge.get('pii_flag',False)).lower()} → 草稿中订单号/邮箱勿外泄给第三方
- threat_signal: {ge.get('threat_signal') or 'none'}
- ambiguous: {str(ge.get('ambiguous',False)).lower()}
- is_conversation_closing: {str(is_closing).lower()}

## Uncertain商品/物流字段求证规则
对于 uncertain_fields 中的商品/物流相关字段（intents[i].related_products/related_orders、products、orders、customer_region），若该信息被用于草稿回复，必须在草稿中向客户求证（如「Are you asking about order #12345?」「Are you located in CA, US?」），除非能通过 dispatch-context/get-messages 内部确证。

"""


def resume_cli_checklist(*, env: str, escalation_id: int) -> str:
    cli = cs_bridge_cli_path()
    paths = agent_tool_paths()
    knowledge_bank = hindsight_knowledge_bank_id()
    knowledge_url = hindsight_knowledge_base_url().rstrip("/")
    return f"""# agent_tool_paths
cs_bridge_tool: {paths['cs_bridge_tool']}

{_tool_rules_block(resume=True)}- quickcep_session_id is the long numeric QuickCEP id — never use CAL internal session id.

# bridge_cli_checklist
0. skill_view(name='povison-cs-escalation-resumer')
1. terminal: python3 {cli} get-escalation --env {env} --escalation-id {escalation_id}
2. terminal: python3 {cli} get-dispatch-context --env {env} --session-id <quickcep_session_id from escalation.session>
3. terminal: python3 {cli} get-messages --env {env} --session-id <quickcep_session_id>
4. terminal: write_file(path='/tmp/draft-<quickcep_session_id>.html', content='<English reply merging operator_answer, Povison tone>')
5. **[REQUIRED] Record Q&A to Hindsight Knowledge bank** BEFORE draft-save, via the `knowledge_retain` MCP tool. This step is NOT optional — skipping it means future identical questions will be re-escalated:
   call knowledge_retain(env="{env}", source="human_confirmed", question="<customer question, may include order/email context — tool will de-identify>", answer="<operator/expert answer>", sku="<SKU if known>", product_name="<optional>", escalation_id="{escalation_id}", session_id="<quickcep_session_id>")
   The tool refines (de-PII + dual-domain structured metadata + reusable check) and retains to the {knowledge_bank} bank. If it returns status="skipped" (e.g. one-off compensation, session narrative), that is correct — continue to draft-save anyway.
   If the MCP server is unavailable, retry via terminal with the direct API curl (items + 300s timeout):
   terminal: curl -s -m 300 -X POST {knowledge_url}/v1/default/banks/{knowledge_bank}/memories -H 'Content-Type: application/json' -d '{{"async":true,"items":[{{"content":"<de-identified Q&A>","context":"povison escalation Q&A","tags":["povison","escalation-qa"],"timestamp":"unset","observation_scopes":"shared"}}]}}'
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
        "apply-handoff --phase must be one of: processing, draft_ready, awaiting_expert, failed, skipped, "
        "reviewed, followup_while_busy, operator_sent — never invent phase names. "
        "Use skipped (not failed) for B2B/spam/carrier misroute intentional no-reply."
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
        "the Hindsight Knowledge bank via the `knowledge_retain` MCP tool (cs-hindsight-knowledge server, see step 5 in the checklist) — "
        "called as a tool, NOT via execute_code. The tool de-identifies PII, judges dual-domain metadata, "
        "and retains only reusable product/policy facts. If it returns status=\"skipped\" (one-off "
        "compensation / session narrative), that is correct — continue to draft-save. If the MCP server "
        "is unavailable, retry via terminal with the direct API curl command (items + 300s, see checklist). "
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
        "STRICT TOOL CONSTRAINT: this run is guard-locked to the Hindsight knowledge tools only. You may call "
        "knowledge_recall to check what's already known in the Knowledge bank (furniture-knowledge), and "
        "knowledge_retain to persist each factual correction as a distinct reusable fact. The tool de-identifies "
        "PII and judges dual-domain (product/policy) metadata for you — pass the raw correction text. You MUST NOT call any "
        "other tool — no terminal, no execute_code, no cs_bridge_tool, no send_message, no hindsight_retain/recall/reflect. "
        "If no factual product/policy change exists, retain nothing and finish.\n\n"
        "When retaining, write concise, self-contained facts (e.g. \"产品X的承重为150kg，非200kg\"), "
        "passing the product/SKU context via the sku/product_name parameters when available. Never retain PII or customer-specific details."
    )
