"""Agent contract strings for cs-ops-bridge gateway briefs."""

from __future__ import annotations

from pathlib import Path


def _cli_path() -> str:
    return str(
        Path(__file__).resolve().parent / "scripts" / "cs_bridge_tool.py"
    )


def process_cli_checklist(*, env: str, quickcep_session_id: str) -> str:
    cli = _cli_path()
    return f"""# bridge_cli_checklist
1. python3 {cli} get-dispatch-context --env {env} --session-id {quickcep_session_id}
2. python3 {cli} classify-intent --env {env} --subject "<subject>" --body "<body>"
3. (auto_handle) product/logistics lookup skills → quickcep_cli draft-save
4. (escalate) send_message to feishu:AI客服后援 → python3 {cli} open-escalation ...
5. python3 {cli} write-event --env {env} --session-id {quickcep_session_id} --event-type <type> --json '{{...}}'
6. python3 {cli} update-session-status --env {env} --session-id {quickcep_session_id} --status draft_ready|awaiting_expert|reviewed
"""


def resume_cli_checklist(*, env: str, escalation_id: int) -> str:
    cli = _cli_path()
    return f"""# bridge_cli_checklist
1. python3 {cli} get-escalation --env {env} --escalation-id {escalation_id}
2. python3 {cli} get-dispatch-context --env {env} --session-id <from escalation.session>
3. Apply operator_answer → quickcep_cli draft-save (never send-email)
4. python3 {cli} write-event --env {env} --session-id <id> --event-type escalation_resumed --json '{{...}}'
5. python3 {cli} update-session-status --env {env} --session-id <id> --status draft_ready
"""


def process_instructions() -> str:
    return (
        "You are running the povison-cs-orchestrator-flow skill for an inbound QuickCEP email. "
        "Use cs_bridge_tool.py for all bridge writes. Use quickcep_cli.py for QuickCEP API. "
        "NEVER send-email to customers; only draft-save. Escalate via Feishu when classify-intent "
        "returns route=escalate or business rules require human input."
    )


def resume_instructions() -> str:
    return (
        "You are running povison-cs-escalation-resumer after an operator answered in Feishu. "
        "Load escalation context via cs_bridge_tool.py, incorporate operator_answer into the "
        "customer reply, and write a QuickCEP draft for human review."
    )
