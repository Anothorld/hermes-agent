"""Normalize customer email draft bodies for QuickCEP HTML editor."""

from __future__ import annotations

import re

_BLOCK_TAG_RE = re.compile(r"<(p|br|div|ul|ol|li|h[1-6])\b", re.I)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def html_to_plain(text: str) -> str:
    """Strip HTML tags; preserve paragraph breaks as newlines."""
    if not text:
        return ""
    # Block closers → newline before strip
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", "", text).strip()


def _markdown_bold_to_strong(text: str) -> str:
    return _MARKDOWN_BOLD_RE.sub(r"<strong>\1</strong>", text)


def text_to_html(text: str) -> str:
    """Plain text → HTML paragraphs; single newlines within a block become <br>."""
    if not text:
        return "<p></p>"
    paragraphs = text.split("\n\n")
    parts: list[str] = []
    for block in paragraphs:
        block = block.strip()
        if not block:
            continue
        lines = [_markdown_bold_to_strong(line.strip()) for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if len(lines) == 1:
            parts.append(f"<p>{lines[0]}</p>")
        else:
            parts.append(f"<p>{'<br>'.join(lines)}</p>")
    return "".join(parts) if parts else "<p></p>"


def has_block_html(text: str) -> bool:
    return bool(_BLOCK_TAG_RE.search(text))


def normalize_draft_html(content: str) -> str:
    """Ensure draft body uses block HTML so QuickCEP shows paragraphs and line breaks."""
    if not content or not str(content).strip():
        return "<p></p>"
    stripped = str(content).strip()
    if not stripped.startswith("<"):
        return text_to_html(stripped)
    if has_block_html(stripped):
        return stripped
    plain = html_to_plain(stripped)
    return text_to_html(plain) if plain else stripped
