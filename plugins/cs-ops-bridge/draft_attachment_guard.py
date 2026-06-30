"""Guard draft-save attachments — only vault-sourced PDFs may attach to customer emails."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_PLUGIN_ROOT = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _PLUGIN_ROOT / "config" / "attachment_guard.yaml"


def guard_enabled() -> bool:
    raw = os.environ.get("CS_OPS_ATTACHMENT_GUARD", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _load_config() -> dict[str, Any]:
    path = Path(os.environ.get("CS_OPS_ATTACHMENT_GUARD_FILE", str(_DEFAULT_CONFIG)))
    if not path.is_file():
        return {"enabled": True, "blocked_pdf_url_patterns": []}
    if yaml is None:
        return {"enabled": True, "blocked_pdf_url_patterns": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _is_pdf_attachment(item: dict[str, Any]) -> bool:
    name = str(item.get("fileName") or item.get("name") or "").strip()
    url = str(item.get("url") or "").strip()
    if name.lower().endswith(".pdf"):
        return True
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def attachments_contain_pdf(attachments_json: str | None) -> bool:
    if not attachments_json:
        return False
    try:
        items = json.loads(attachments_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(items, list):
        return False
    return any(isinstance(it, dict) and _is_pdf_attachment(it) for it in items)


def _parse_attachments(attachments_json: str | None) -> list[dict[str, Any]]:
    if not attachments_json:
        return []
    try:
        items = json.loads(attachments_json)
    except json.JSONDecodeError:
        return []
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def guard_draft_attachments(
    attachments_json: str | None,
    *,
    allowed_attachment_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Return guard result dict with blocked, error, blocked_kind keys."""
    if not guard_enabled():
        return {"blocked": False, "matches": [], "error": "", "blocked_kind": ""}

    cfg = _load_config()
    if not cfg.get("enabled", True):
        return {"blocked": False, "matches": [], "error": "", "blocked_kind": ""}

    items = _parse_attachments(attachments_json)
    pdf_items = [it for it in items if _is_pdf_attachment(it)]
    if not pdf_items:
        return {"blocked": False, "matches": [], "error": "", "blocked_kind": ""}

    allowed_set = {u.strip().rstrip("/") for u in (allowed_attachment_urls or []) if u}
    blocked_patterns = []
    for pat in cfg.get("blocked_pdf_url_patterns") or []:
        try:
            blocked_patterns.append(re.compile(pat, re.IGNORECASE))
        except re.error:
            blocked_patterns.append(re.compile(re.escape(str(pat)), re.IGNORECASE))

    for item in pdf_items:
        name = str(item.get("fileName") or item.get("name") or "attachment.pdf")
        url = str(item.get("url") or "").strip()
        if not url:
            return {
                "blocked": True,
                "matches": [name],
                "error": "Attachment guard: PDF attachment missing URL",
                "blocked_kind": "pdf_missing_url",
            }
        for pat in blocked_patterns:
            if pat.search(url):
                return {
                    "blocked": True,
                    "matches": [url],
                    "error": (
                        "Attachment guard: product/assembly PDF cannot be sent as email attachment — "
                        "extract text into the email body instead"
                    ),
                    "error_detail": f"Blocked PDF: {name}",
                    "blocked_kind": "pdf_product_url",
                    "source": "attachments",
                }
        normalized = url.strip().rstrip("/")
        if normalized not in allowed_set:
            return {
                "blocked": True,
                "matches": [url],
                "error": (
                    "Attachment guard: PDF attachments must come from ESC vault upload for this session"
                ),
                "error_detail": (
                    f"Blocked PDF: {name} — URL not in allowed_attachment_urls. "
                    "Extract text into email body instead."
                ),
                "blocked_kind": "pdf_not_vault",
                "source": "attachments",
            }

    return {"blocked": False, "matches": [], "error": "", "blocked_kind": ""}
