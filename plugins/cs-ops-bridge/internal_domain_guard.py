"""Internal domain guard — blocks customer-facing drafts from leaking internal URLs.

Scans draft HTML content and attachment URLs for internal/backend domains before
the draft is written to QuickCEP. If any match is found, the draft-save aborts
with a clear error so the agent can strip the internal link and retry.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_PLUGIN_ROOT = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _PLUGIN_ROOT / "config" / "internal_domains.yaml"


def _load_config() -> dict[str, Any]:
    """Load the internal domains config from env override or default path."""
    config_path = os.environ.get(
        "CS_OPS_INTERNAL_DOMAINS_FILE", str(_DEFAULT_CONFIG)
    )
    p = Path(config_path)
    if not p.is_file():
        return {"enabled": True, "blocked_domains": [], "blocked_patterns": []}
    if yaml is None:
        # Fallback: minimal YAML parsing for simple lists
        text = p.read_text(encoding="utf-8")
        domains: list[str] = []
        patterns: list[str] = []
        enabled = True
        section: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("enabled:"):
                enabled = "true" in stripped.lower()
            elif stripped.startswith("blocked_domains:"):
                section = "domains"
            elif stripped.startswith("blocked_patterns:"):
                section = "patterns"
            elif stripped.startswith("- ") and section == "domains":
                domains.append(stripped[2:].strip().strip('"').strip("'"))
            elif stripped.startswith("- ") and section == "patterns":
                patterns.append(stripped[2:].strip().strip('"').strip("'"))
        return {"enabled": enabled, "blocked_domains": domains, "blocked_patterns": patterns}
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"enabled": True, "blocked_domains": [], "blocked_patterns": []}
    return data


def _compile_patterns(domains: list[str], regex_patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile domain substrings (escaped) + regex patterns into a single list."""
    compiled: list[re.Pattern[str]] = []
    for d in domains:
        d = d.strip()
        if d:
            compiled.append(re.compile(re.escape(d), re.IGNORECASE))
    for p in regex_patterns:
        p = p.strip()
        if p:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                # If regex is invalid, treat as literal
                compiled.append(re.compile(re.escape(p), re.IGNORECASE))
    return compiled


def check_content(content: str) -> dict[str, Any]:
    """Check draft HTML content for internal domain leaks.

    Returns a dict:
      - ``blocked`` (bool): True if any internal domain was found
      - ``matches`` (list[str]): Human-readable list of matched patterns
      - ``snippet`` (str): A short context snippet around the first match (for debugging)
    """
    if not content or not str(content).strip():
        return {"blocked": False, "matches": [], "snippet": ""}

    cfg = _load_config()
    if not cfg.get("enabled", True):
        return {"blocked": False, "matches": [], "snippet": ""}

    compiled = _compile_patterns(
        cfg.get("blocked_domains", []),
        cfg.get("blocked_patterns", []),
    )
    if not compiled:
        return {"blocked": False, "matches": [], "snippet": ""}

    text = str(content)
    matches: list[str] = []
    first_snippet = ""

    for pat in compiled:
        m = pat.search(text)
        if m:
            matched_text = m.group(0)
            # Build a readable label: use the original domain/pattern source
            label = matched_text
            if label not in matches:
                matches.append(label)
            if not first_snippet:
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                first_snippet = text[start:end].replace("\n", " ")

    return {
        "blocked": len(matches) > 0,
        "matches": matches,
        "snippet": first_snippet,
    }


def check_attachments(attachments_json: str | None) -> dict[str, Any]:
    """Check attachment URLs for internal domain leaks.

    ``attachments_json`` is the JSON string passed to ``--attachments`` flag.
    Returns same shape as :func:`check_content`.
    """
    if not attachments_json:
        return {"blocked": False, "matches": [], "snippet": ""}

    import json

    try:
        attachments: list[dict[str, Any]] = json.loads(attachments_json)
    except (json.JSONDecodeError, TypeError):
        # If it's not valid JSON, let the downstream quickcep_cli handle the error
        return {"blocked": False, "matches": [], "snippet": ""}

    all_urls: list[str] = []
    for att in attachments:
        if isinstance(att, dict):
            url = att.get("url", "")
            if url:
                all_urls.append(str(url))

    if not all_urls:
        return {"blocked": False, "matches": [], "snippet": ""}

    combined = "\n".join(all_urls)
    return check_content(combined)


def guard_draft(content: str, attachments_json: str | None = None) -> dict[str, Any]:
    """Combined guard: check both content and attachments.

    Returns a dict with:
      - ``blocked`` (bool)
      - ``matches`` (list[str])
      - ``source`` (str): "content", "attachments", or ""
      - ``snippet`` (str)
      - ``error`` (str): Ready-to-print error message if blocked
    """
    content_result = check_content(content)
    att_result = check_attachments(attachments_json)

    all_matches: list[str] = []
    sources: list[str] = []
    snippet = ""

    if content_result["blocked"]:
        all_matches.extend(content_result["matches"])
        sources.append("content")
        snippet = content_result["snippet"]
    if att_result["blocked"]:
        all_matches.extend(att_result["matches"])
        sources.append("attachments")
        if not snippet:
            snippet = att_result["snippet"]

    # Deduplicate matches while preserving order
    seen: set[str] = set()
    unique_matches: list[str] = []
    for m in all_matches:
        if m not in seen:
            seen.add(m)
            unique_matches.append(m)

    if not unique_matches:
        return {"blocked": False, "matches": [], "source": "", "snippet": "", "error": ""}

    source_str = " + ".join(sources)
    error_msg = (
        f"Internal domain guard: draft blocked — internal URLs detected in {source_str}. "
        f"Matched patterns: {', '.join(unique_matches)}. "
        f"Remove all internal/backend links before saving the draft."
    )

    return {
        "blocked": True,
        "matches": unique_matches,
        "source": source_str,
        "snippet": snippet,
        "error": error_msg,
    }
