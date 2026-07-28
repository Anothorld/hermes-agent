"""FastMCP server exposing povison-cs dedicated Hindsight knowledge tools.

Tools (both @mcp.tool):
- knowledge_retain: refine (de-PII + dual-domain structured metadata + reusable) then HTTP retain to Knowledge bank.
- knowledge_recall: Intent+Attribute Parser -> HTTP recall -> credibility/time rerank.

Bank isolation: bank_id is hardcoded to the Knowledge bank; tools do not accept a bank override.

Run locally:
  python3 cs_hindsight_knowledge.py
  fastmcp inspect cs_hindsight_knowledge.py:mcp
  fastmcp call cs_hindsight_knowledge.py knowledge_retain --json ...

Env:
  HINDSIGHT_BASE_URL (default http://192.168.10.123:8888)
  CS_OPS_HINDSIGHT_KNOWLEDGE_BANK (default furniture-knowledge)
  CS_HINDSIGHT_LLM_API_KEY / CS_HINDSIGHT_LLM_BASE_URL / CS_HINDSIGHT_LLM_MODEL (optional; rule fallback if absent)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# Prefer the `mcp` package FastMCP already shipped in the hermes-agent image
# (standalone `fastmcp` 3.x is a local-dev convenience and may be absent in Docker).
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover — local machines with only standalone fastmcp
    from fastmcp import FastMCP

_HERE = Path(__file__).resolve().parent
_PLUGIN_ROOT = _HERE.parent


def _load_hindsight_ko():
    """Load hindsight_ko.py from the plugin root (works whether run as script or package)."""
    mod_name = "cs_ops_hindsight_ko_loaded"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _PLUGIN_ROOT / "hindsight_ko.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    sys.modules[mod_name] = mod
    return mod


_ko = _load_hindsight_ko()

mcp = FastMCP("cs-hindsight-knowledge")


@mcp.tool()
def knowledge_retain(env: str, source: str, question: str, answer: str,
                     sku: str = "", product_name: str = "", product_slug: str = "",
                     escalation_id: str = "", session_id: str = "",
                     force_retain: bool = False) -> dict[str, Any]:
    """Refine a customer-service Q&A (de-identify PII, judge dual-domain metadata, reusable check)
    and retain it to the povison-cs Knowledge bank.

    Parameters:
    - env: "TEST" or "LIVE"
    - source: official_pdf | official_policy | human_confirmed | user_reported
    - question/answer: raw expert Q/A (may contain order#/email; tool will de-identify)
    - sku/product_name/product_slug: product context if available
    - escalation_id/session_id: only stored in metadata.evidence, never in searchable content
    - force_retain: operator override (skip reusable/PII gates); default false

    Returns {"status":"retained"|"skipped"|"error", ...}. Skipped still lets the agent proceed to draft-save.
    """
    return _ko.retain_ko(
        raw_question=question, raw_answer=answer,
        sku=sku or None, product_name=product_name or None, product_slug=product_slug or None,
        escalation_id=escalation_id or None, session_id=session_id or None,
        env=env, source=source, force_retain=force_retain, async_=True,
    )


@mcp.tool()
def knowledge_recall(question: str, sku: str = "", product_name: str = "",
                      max_tokens: int = 4096, budget: str = "mid") -> dict[str, Any]:
    """Recall reusable product/policy knowledge for a customer question.

    Runs an Intent+Attribute Parser to derive domain/product_id/attribute/policy_type,
    builds a recall request (query + tags_match=all + metadata_filter when supported),
    HTTP-calls the Knowledge bank, then reranks by source credibility and policy effective dates.

    Parameters:
    - question: customer question (original phrasing)
    - sku/product_name: product hints if known
    - max_tokens/budget: recall budget knobs

    Returns {"status":"ok"|"error", "parsed":..., "results":[...]}.
    """
    return _ko.recall_ko(
        question=question, sku=sku or None, product_name=product_name or None,
        max_tokens=max_tokens, budget=budget, use_metadata_filter=True,
    )


@mcp.tool()
def knowledge_bank() -> dict[str, Any]:
    """Return the hardcoded Knowledge bank id (bank isolation helper)."""
    return {"bank": _ko.knowledge_bank_id(), "base_url": _ko.HINDSIGHT_BASE_URL}


if __name__ == "__main__":
    mcp.run()
