"""Photo-source guard for AI-generated drafts.

When a customer requests "actual photos" / "real photos" / "warehouse photos"
of a product, the AI must attach QC (quality check) images re-uploaded to the
QuickCEP CDN — NOT hotlink static.povison.com catalog images.

This guard detects drafts that contain ``<img>`` tags pointing to
``static.povison.com`` (product catalog / website images) and blocks them,
forcing the AI to either attach real QC photos or escalate for warehouse
photo sourcing.

Rationale: the customer has already seen the website images. Sending them
again as inline ``<img>`` tags is a quality failure. The correct approach is
to query the QC system (``qc-images-doc-api``), download real photos, re-upload
to QuickCEP CDN, and attach them as files.

This guard is intentionally narrow:
- Only blocks ``<img src="...static.povison.com/...">`` (inline hotlinked images).
- Does NOT block ``<a href="...povison.com/...">`` (product page links are fine).
- Does NOT block images already uploaded to ``quick-cep-cdn.quickcep.com``.
- Does NOT block images from the QC CDN (``musem-scm-public...aliyuncs.com``).

Callers: ``draft_guard.guard_draft_content`` → this module's ``guard_draft``.
"""

from __future__ import annotations

import re
from typing import Any

# Match <img ... src="https://static.povison.com/..." ...>
# Case-insensitive, handles single/double quotes and unquoted attributes.
_STATIC_POVISON_IMG_RE = re.compile(
    r'<img\s[^>]*src\s*=\s*["\']?(https?://static\.povison\.com/[^"\'\s>]+)',
    re.IGNORECASE,
)

_BLOCKED_DOMAIN = "static.povison.com"


def guard_draft(content: str, attachments: Any = None) -> dict[str, Any]:
    """Check draft for hotlinked static.povison.com inline images.

    Returns ``{"blocked": False}`` if the draft passes, or a block payload
    with ``blocked: True`` and diagnostic info.
    """
    if not content:
        return {"blocked": False}

    matches = _STATIC_POVISON_IMG_RE.findall(content)
    if not matches:
        return {"blocked": False}

    # Extract a short snippet for the error message
    snippet = ""
    for m in _STATIC_POVISON_IMG_RE.finditer(content):
        snippet = m.group(0)[:200]
        break

    return {
        "blocked": True,
        "error": "inline catalog image detected",
        "error_detail": (
            "Draft contains <img> tags hotlinked from static.povison.com (product catalog). "
            "When a customer asks for actual/real/warehouse photos, you MUST query the QC system "
            "(qc-images-doc-api), download real photos, re-upload to QuickCEP CDN, and attach as files. "
            "Do NOT hotlink website catalog images in <img> tags. "
            "Product page LINKS (<a href>) are fine; inline <img src=\"static.povison.com/...\"> is not. "
            "If QC images are unavailable, escalate for warehouse photo sourcing."
        ),
        "matches": matches[:5],
        "snippet": snippet,
        "source": "content",
    }
