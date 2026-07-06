"""Advertisement / spam email detection for inbound QuickCEP sessions.

Keyword-based detection of unsolicited marketing, SEO, collaboration,
partnership, and guest-post proposals.  When detected, the watcher tags the
session with the 广告 tag and skips AI processing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

log = logging.getLogger(__name__)

# QuickCEP tag ID for 广告 (Inquiry Nature → 广告)
AD_TAG_ID = "2072600617073577986"

# Keywords that indicate an advertisement / unsolicited outreach email.
# Matching is case-insensitive, whole-word (word-boundary aware).
_AD_KEYWORDS: tuple[str, ...] = (
    "collaboration",
    "collaborate",
    "proposal",
    "tariff",
    "guest post",
    "guestpost",
    "partnership",
    "seo",
)

# Pre-compile regex patterns for each keyword.
# Use \b word boundaries; handle multi-word keywords by escaping spaces.
_AD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    for kw in _AD_KEYWORDS
)


def detect_ad_email(
    *,
    subject: str = "",
    body: str = "",
    content_preview: str = "",
) -> bool:
    """Return True if the email subject or body matches ad keywords.

    Args:
        subject: Email subject line.
        body: Full email body text (plain text, not HTML).
        content_preview: Short content preview (e.g. first 300 chars from SIO).

    The function checks subject + body + content_preview combined.
    Any single keyword hit triggers detection.
    """
    # Combine all available text for matching.
    combined = " ".join(filter(None, [subject, body, content_preview]))
    if not combined.strip():
        return False
    combined_lower = combined.lower()
    for pattern in _AD_PATTERNS:
        if pattern.search(combined_lower):
            log.info(
                "ad_email_detected keyword=%s subject=%s",
                pattern.pattern,
                (subject or "")[:80],
            )
            return True
    return False


def detect_ad_from_info(info: Mapping[str, Any]) -> bool:
    """Convenience wrapper: detect ad from watcher info dict.

    Extracts subject and content_preview from the info dict produced by
    either the SIO monitor or the REST reconcile path.

    For SIO: info has 'email_subject' and 'content_preview'.
    For REST: info may lack these; caller should pass lastMsgContent-derived
    subject/preview via info.get('email_subject') / info.get('content_preview')
    before calling this function.
    """
    subject = str(info.get("email_subject") or "")
    content_preview = str(info.get("content_preview") or "")

    # Also check 'from' field — some ad emails have keyword in sender name.
    from_field = str(info.get("from") or "")
    if from_field:
        # Check if sender name contains ad keywords (not the email address itself)
        # e.g. "SEO Agency <noreply@example.com>"
        sender_name = from_field.split("<")[0].strip() if "<" in from_field else ""
        if sender_name:
            combined = f"{subject} {sender_name} {content_preview}"
        else:
            combined = f"{subject} {content_preview}"
    else:
        combined = f"{subject} {content_preview}"

    return detect_ad_email(
        subject=subject,
        body=combined,
        content_preview=content_preview,
    )


def parse_rest_last_msg_content(row: Mapping[str, Any]) -> tuple[str, str]:
    """Extract email_subject and content from a REST session row's lastMsgContent.

    Returns (subject, content_preview) — both may be empty strings.
    """
    lmc = row.get("lastMsgContent")
    if not lmc:
        return "", ""
    try:
        parsed = json.loads(lmc) if isinstance(lmc, str) else lmc
    except (json.JSONDecodeError, TypeError):
        return "", ""
    if not isinstance(parsed, dict):
        return "", ""
    subject = str(parsed.get("emailSubject") or "")
    # lastMsgContent may have 'content' (email body) — often empty in list API.
    content = str(parsed.get("content") or "")
    return subject, content


# Tag names that indicate an ad/spam session in QuickCEP subSessionTags.
_AD_TAG_NAMES: frozenset[str] = frozenset({"广告", "Advertising", "Marketing"})


def has_ad_tag(row: Mapping[str, Any]) -> bool:
    """Check if a REST session row already has an ad-related tag.

    QuickCEP's subSessionTags field returns tag names (not IDs).
    Returns True if any tag name matches known ad-tag names.
    """
    tags = row.get("subSessionTags")
    if not tags or not isinstance(tags, (list, tuple)):
        return False
    for tag in tags:
        if isinstance(tag, str):
            for ad_name in _AD_TAG_NAMES:
                if ad_name.lower() in tag.lower():
                    return True
    return False
