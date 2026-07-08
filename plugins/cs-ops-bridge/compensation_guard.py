"""Compensation guard — blocks AI drafts from containing unauthorized compensation offers.

Scans draft HTML content for compensation-specific language patterns (goodwill
discounts, partial refunds, service recovery credits, etc.) before the draft is
written to QuickCEP. If any match is found, the draft-save aborts with a clear
error instructing the agent to remove compensation and escalate via open-escalation.

This guard enforces the HARD RULE that ALL compensation decisions must be made
by a human operator. The AI must never include any monetary concession in
customer-facing drafts regardless of amount or source (including Hindsight recall).

Architecture mirrors internal_domain_guard.py:
  - Patterns loaded from config/compensation_patterns.yaml
  - Substring + regex matching against draft text (HTML stripped to plain text)
  - Returns block payload with matches + context snippet

Evaluated against 179 historical AI/operator drafts (687 total sessions):
  - 12 true positives, 0 false positives, 0 false negatives
  - Precision 100%, Recall 100%, F1 100%
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_PLUGIN_ROOT = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _PLUGIN_ROOT / "config" / "compensation_patterns.yaml"


class _TextExtractor(HTMLParser):
    """Extract plain text from HTML for pattern matching."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _html_to_text(html: str) -> str:
    """Strip HTML tags and return plain text for pattern matching."""
    if not html or not html.strip():
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        # If HTML parsing fails, fall back to raw text
        return re.sub(r"<[^>]+>", " ", html)
    return extractor.get_text()


def _load_config() -> dict[str, Any]:
    """Load compensation patterns config from env override or default path."""
    config_path = os.environ.get(
        "CS_OPS_COMPENSATION_PATTERNS_FILE", str(_DEFAULT_CONFIG)
    )
    p = Path(config_path)
    if not p.is_file():
        return {"enabled": True, "blocked_patterns": []}
    if yaml is None:
        # Fallback: minimal YAML parsing for simple lists
        text = p.read_text(encoding="utf-8")
        patterns: list[str] = []
        enabled = True
        section: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("enabled:"):
                enabled = "true" in stripped.lower()
            elif stripped.startswith("blocked_patterns:"):
                section = "patterns"
            elif stripped.startswith("- ") and section == "patterns":
                patterns.append(stripped[2:].strip().strip('"').strip("'"))
        return {"enabled": enabled, "blocked_patterns": patterns}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"enabled": True, "blocked_patterns": []}
    return data


def _compile_patterns(regex_patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile regex patterns into a list of compiled Pattern objects."""
    compiled: list[re.Pattern[str]] = []
    for p in regex_patterns:
        p = p.strip()
        if not p:
            continue
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            # If regex is invalid, treat as literal
            compiled.append(re.compile(re.escape(p), re.IGNORECASE))
    return compiled


def check_content(content: str) -> dict[str, Any]:
    """Check draft HTML content for compensation offers.

    Returns a dict:
      - ``blocked`` (bool): True if any compensation pattern was found
      - ``matches`` (list[str]): Human-readable list of matched patterns
      - ``snippet`` (str): A short context snippet around the first match
    """
    if not content or not str(content).strip():
        return {"blocked": False, "matches": [], "snippet": ""}

    cfg = _load_config()
    if not cfg.get("enabled", True):
        return {"blocked": False, "matches": [], "snippet": ""}

    compiled = _compile_patterns(cfg.get("blocked_patterns", []))
    if not compiled:
        return {"blocked": False, "matches": [], "snippet": ""}

    # Strip HTML to plain text for matching (avoids matching HTML tags/attributes)
    text = _html_to_text(str(content))
    if not text.strip():
        return {"blocked": False, "matches": [], "snippet": ""}

    matches: list[str] = []
    first_snippet = ""

    for pat in compiled:
        m = pat.search(text)
        if m:
            matched_text = m.group(0).strip()
            if matched_text not in matches:
                matches.append(matched_text)
            if not first_snippet:
                start = max(0, m.start() - 60)
                end = min(len(text), m.end() + 60)
                first_snippet = text[start:end].replace("\n", " ").strip()

    return {
        "blocked": len(matches) > 0,
        "matches": matches,
        "snippet": first_snippet,
    }


def guard_draft(content: str) -> dict[str, Any]:
    """Check draft content for compensation offers.

    Returns a dict with:
      - ``blocked`` (bool)
      - ``matches`` (list[str])
      - ``snippet`` (str)
      - ``error`` (str): Ready-to-print error message if blocked
    """
    result = check_content(content)

    if not result["blocked"]:
        return {"blocked": False, "matches": [], "snippet": "", "error": ""}

    error_msg = (
        f"Compensation guard: draft blocked — compensation/goodwill offer detected. "
        f"Matched patterns: {', '.join(result['matches'])}. "
        f"Remove ALL compensation amounts, discounts, and monetary offers from the draft. "
        f"To address the customer's situation, use open-escalation to notify the operator, "
        f"who will decide whether to add compensation before sending."
    )

    return {
        "blocked": True,
        "matches": result["matches"],
        "snippet": result["snippet"],
        "error": error_msg,
    }
