"""Gateway HTTP client for inbound reply agent dispatch."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def _classifier_handoff_brief_block() -> str:
    """Load handoff brief; supports package import and direct module load."""
    try:
        from ..bridge_agent_contract import classifier_handoff_brief_block

        return classifier_handoff_brief_block()
    except ImportError:
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "_bridge_agent_contract_for_gateway",
            root / "bridge_agent_contract.py",
        )
        if spec is None or spec.loader is None:
            return ""
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.classifier_handoff_brief_block()


def dispatcher_instructions() -> str:
    bridge_cli = (
        Path(__file__).resolve().parents[1] / "scripts" / "kol_bridge_tool.py"
    )
    base = (
        "You are running the `kol-reply-dispatcher` skill. Read the supplied "
        "pending_replies array and dispatch context, classify the inbound reply, "
        "persist classifier facts via the bridge CLI, then follow the skill's "
        "multi-goal flow: `select-draftable-plan` → parallel fragment-mode child "
        "skills (one per draftable goal) → merge disjoint `proposed_facts` with a "
        "single `write-facts-multi` → `kol-reply-synthesizer` → "
        "`persist-reply-draft` with `contributing` list and top-level "
        "`conversation_summary.bullets` (Chinese operator thread recap). "
        "Open escalations for "
        "human-gate goals and fragment `gate:true` results; do not send mail. "
        "Respect each reply's `anomaly_signals` soft-control flags "
        "(especially allow_autoflow / gate_budget / gate_contract / gate_payout). "
        "For idempotency labels, use only `kol_bridge_tool.py mark-reply-handled`; "
        "do not call Gmail label APIs or custom scripts directly. "
        f"MANDATORY bridge CLI: python3 {bridge_cli} <subcommand> --env <env> ... "
        "Always use python3 (never bare `python`). Use this absolute path from any cwd. "
        "Routing/facts/drafts: use only kol_bridge_tool.py subcommands documented in "
        "kol-reply-dispatcher/references/shared/bridge-http-api-endpoints.md — "
        "never import kol-ops-bridge Python modules or use execute_code "
        "for Bridge HTTP."
    )
    return f"{base}\n\n{_classifier_handoff_brief_block()}"


@dataclass
class GatewayClient:
    base: str
    api_key: Optional[str]

    @classmethod
    def from_env(cls) -> GatewayClient:
        return cls(
            base=os.environ.get("HERMES_GATEWAY_BASE", "http://127.0.0.1:8642").rstrip("/"),
            api_key=os.environ.get("HERMES_GATEWAY_KEY"),
        )

    def run(self, *, instructions: str, input_text: str, session_id: str) -> Optional[str]:
        body = {
            "input": input_text,
            "instructions": instructions,
            "session_id": session_id,
            "conversation_history": [],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            out = _http_json(
                "POST",
                f"{self.base}/v1/runs",
                headers=headers,
                body=body,
                timeout=30.0,
            )
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            log.error("gateway run failed for %s: %s", session_id, exc)
            return None
        return out.get("run_id") if isinstance(out, dict) else None


def _http_json(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    payload: Optional[bytes] = None
    hdrs: dict[str, str] = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"_raw": raw.decode("utf-8", "replace")}
