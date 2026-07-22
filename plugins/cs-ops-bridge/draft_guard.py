"""Shared draft guard (PR1.9).

Single entry point combining the internal-domain guard, the PDF attachment
guard, and the compensation guard, reused by every draft-writing path so the
Console composer, the agent (cs_bridge_tool draft-save), and the service
send-reply all enforce the same policy:

- ``guard_draft_content(content, attachments, allowed_attachment_urls)`` →
  returns ``None`` when the draft is allowed, or a block payload dict
  (``{blocked, error, error_detail, source, matches, snippet, blocked_kind}``)
  when it must be refused.

Callers:
- PUT /sessions/{id}/draft (server-side guard for Console-originated drafts)
- send_reply._guard_draft (service send path, defense in depth)
- cs_bridge_tool draft-save (agent path, kept for the legacy QuickCEP branch)
"""

from __future__ import annotations

import json
from typing import Any, Optional

# This module is imported both as a package member (``from .draft_guard import``)
# and as a top-level module by cs_bridge_tool (``from draft_guard import`` with
# the plugin root on sys.path). Support both by trying relative then absolute.
try:
    from .internal_domain_guard import guard_draft as _guard_domain  # type: ignore
    from .draft_attachment_guard import (  # type: ignore
        attachments_contain_pdf as _attachments_contain_pdf,
        guard_draft_attachments as _guard_draft_attachments,
    )
    from .compensation_guard import guard_draft as _guard_compensation  # type: ignore
    from .link_guard import guard_draft as _guard_link  # type: ignore
    from .photo_source_guard import guard_draft as _guard_photo_source  # type: ignore
    _PKG_CONTEXT = True
except ImportError:  # loaded as a top-level module
    from internal_domain_guard import guard_draft as _guard_domain  # type: ignore
    from draft_attachment_guard import (  # type: ignore
        attachments_contain_pdf as _attachments_contain_pdf,
        guard_draft_attachments as _guard_draft_attachments,
    )
    from compensation_guard import guard_draft as _guard_compensation  # type: ignore
    from link_guard import guard_draft as _guard_link  # type: ignore
    from photo_source_guard import guard_draft as _guard_photo_source  # type: ignore
    _PKG_CONTEXT = False


def _attachments_to_json(attachments: Any) -> Optional[str]:
    if attachments is None:
        return None
    if isinstance(attachments, str):
        return attachments
    try:
        return json.dumps(attachments, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def guard_draft_content(
    content: str,
    attachments: Any = None,
    *,
    allowed_attachment_urls: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Run internal-domain + compensation + PDF attachment guards on a draft.

    Returns ``None`` if the draft passes, else a block payload dict suitable for
    raising as an HTTP 422 detail or returning to the FE.
    """
    attachments_json = _attachments_to_json(attachments)

    # 1. Internal-domain guard (content + attachment URLs).
    if _guard_domain is not None:
        res = _guard_domain(content, attachments_json)
        if res.get("blocked"):
            return {
                "blocked": True,
                "error": res.get("error", "internal domain blocked"),
                "error_detail": f"Matched: {', '.join(res.get('matches') or [])}",
                "source": res.get("source", "content"),
                "matches": res.get("matches") or [],
                "snippet": res.get("snippet", ""),
                "blocked_kind": "",
            }

    # 2. Compensation guard — block AI drafts containing compensation offers.
    if _guard_compensation is not None:
        res = _guard_compensation(content)
        if res.get("blocked"):
            return {
                "blocked": True,
                "error": res.get("error", "compensation offer blocked"),
                "error_detail": f"Matched: {', '.join(res.get('matches') or [])}",
                "source": "content",
                "matches": res.get("matches") or [],
                "snippet": res.get("snippet", ""),
                "blocked_kind": "compensation",
            }

    # 3. Link guard — block AI drafts containing broken (404/5xx) povison.com links.
    if _guard_link is not None:
        res = _guard_link(content)
        if res.get("blocked"):
            return {
                "blocked": True,
                "error": res.get("error", "broken link blocked"),
                "error_detail": f"Matched: {', '.join(res.get('matches') or [])}",
                "source": "content",
                "matches": res.get("matches") or [],
                "snippet": res.get("snippet", ""),
                "blocked_kind": "link",
            }

    # 4. Photo-source guard — block drafts hotlinking static.povison.com catalog images.
    if _guard_photo_source is not None:
        res = _guard_photo_source(content)
        if res.get("blocked"):
            return {
                "blocked": True,
                "error": res.get("error", "catalog image blocked"),
                "error_detail": res.get("error_detail", ""),
                "source": res.get("source", "content"),
                "matches": res.get("matches") or [],
                "snippet": res.get("snippet", ""),
                "blocked_kind": "photo_source",
            }

    # 5. PDF attachment guard (vault-sourced PDFs only on escalation resume).
    if _attachments_contain_pdf is not None and _guard_draft_attachments is not None:
        if _attachments_contain_pdf(attachments_json):
            res = _guard_draft_attachments(attachments_json, allowed_attachment_urls=allowed_attachment_urls)
            if res.get("blocked"):
                return {
                    "blocked": True,
                    "error": res.get("error", "attachment blocked"),
                    "error_detail": res.get("error_detail", ""),
                    "source": res.get("source", "attachments"),
                    "matches": [],
                    "snippet": "",
                    "blocked_kind": res.get("blocked_kind", ""),
                }

    return None
