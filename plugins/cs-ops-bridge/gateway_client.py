"""Gateway HTTP client for cs-ops-bridge watchers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .bridge_agent_contract import process_instructions, resume_instructions
from .gateway_launch import (
    drain_run_events,
    launch_dedup_key,
    post_run_with_retry,
    release_launch,
    try_acquire_launch,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaunchOutcome:
    run_id: str | None
    dedup_skipped: bool = False


@dataclass
class GatewayClient:
    base: str
    api_key: str | None

    @classmethod
    def from_env(cls) -> GatewayClient:
        return cls(
            base=os.environ.get("CS_OPS_GATEWAY_BASE", "http://127.0.0.1:8643").rstrip("/"),
            api_key=os.environ.get("HERMES_GATEWAY_KEY") or os.environ.get("API_SERVER_KEY"),
        )

    def start_process_run(
        self,
        *,
        quickcep_session_id: str,
        env: str,
        message_id: str,
        brief_extra: str = "",
    ) -> LaunchOutcome:
        from .bridge_agent_contract import process_cli_checklist

        session_id = f"povison-cs:{env}:{quickcep_session_id}"
        brief = (
            f"# cs_inbound_process\n"
            f"env: {env}\n"
            f"quickcep_session_id: {quickcep_session_id}\n"
            f"message_id: {message_id}\n"
            f"{brief_extra}\n"
            f"{process_cli_checklist(env=env, quickcep_session_id=quickcep_session_id)}"
        )
        return self._start_run(
            input_text=brief,
            instructions=process_instructions(),
            session_id=session_id,
            dedup_message_id=message_id,
        )

    def start_resume_run(
        self,
        *,
        escalation_id: int,
        quickcep_session_id: str,
        env: str,
        operator_answer: str,
    ) -> LaunchOutcome:
        from .bridge_agent_contract import resume_cli_checklist

        session_id = f"povison-cs:{env}:{quickcep_session_id}"
        brief = (
            f"# escalation_resume\n"
            f"escalation_id: {escalation_id}\n"
            f"env: {env}\n"
            f"quickcep_session_id: {quickcep_session_id}\n"
            f"operator_answer:\n{operator_answer}\n"
            f"{resume_cli_checklist(env=env, escalation_id=escalation_id)}"
        )
        return self._start_run(
            input_text=brief,
            instructions=resume_instructions(),
            session_id=session_id,
            dedup_message_id=f"resume:{escalation_id}",
        )

    def _start_run(
        self,
        *,
        input_text: str,
        instructions: str,
        session_id: str,
        dedup_message_id: str,
    ) -> LaunchOutcome:
        dedup = launch_dedup_key(session_id, dedup_message_id)
        if not try_acquire_launch(dedup):
            log.info("launch dedup skip %s", dedup)
            return LaunchOutcome(run_id=None, dedup_skipped=True)
        try:
            body = {
                "input": input_text,
                "instructions": instructions,
                "session_id": session_id,
                "conversation_history": [],
            }
            out = post_run_with_retry(base=self.base, api_key=self.api_key, body=body)
            if not out:
                return LaunchOutcome(run_id=None)
            run_id = out.get("run_id") if isinstance(out, dict) else None
            if run_id:
                drain_run_events(base=self.base, api_key=self.api_key, run_id=str(run_id))
            return LaunchOutcome(run_id=str(run_id) if run_id else None)
        finally:
            release_launch(dedup)
