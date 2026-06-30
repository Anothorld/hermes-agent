"""Download image attachments from Feishu escalation reply threads."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from .feishu_client import download_message_resource

log = logging.getLogger(__name__)


def _parse_message_body(item: dict[str, Any]) -> dict[str, Any]:
    body = item.get("body") or {}
    content = body.get("content") if isinstance(body, dict) else None
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}
    return {}


def _image_keys_from_post(content: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for row in content.get("content") or []:
        if not isinstance(row, list):
            continue
        for cell in row:
            if not isinstance(cell, dict):
                continue
            tag = cell.get("tag")
            if tag == "img":
                key = cell.get("image_key")
                if key:
                    keys.append(str(key))
    return keys


def extract_feishu_thread_images(
    messages: list[dict[str, Any]],
    *,
    token: str,
    after_ms: int = 0,
    exclude_message_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Download image bytes from Feishu thread messages (images only, not PDF files)."""
    exclude = exclude_message_ids or set()
    out: list[dict[str, Any]] = []

    for msg in messages:
        mid = str(msg.get("message_id") or "")
        if not mid or mid in exclude:
            continue
        try:
            ts = int(str(msg.get("create_time") or "0"))
        except ValueError:
            ts = 0
        if after_ms and ts <= after_ms:
            continue

        msg_type = str(msg.get("msg_type") or "")
        content = _parse_message_body(msg)

        image_keys: list[str] = []
        if msg_type == "image":
            key = content.get("image_key")
            if key:
                image_keys.append(str(key))
        elif msg_type == "post":
            image_keys.extend(_image_keys_from_post(content))
        elif msg_type == "file":
            file_name = str(content.get("file_name") or "")
            if file_name.lower().endswith(".pdf"):
                continue
            key = content.get("file_key")
            if key and not file_name.lower().endswith(".pdf"):
                image_keys.append(str(key))

        for image_key in image_keys:
            data = download_message_resource(
                message_id=mid,
                file_key=image_key,
                resource_type="image",
                token=token,
            )
            if not data:
                continue
            md5 = hashlib.md5(data).hexdigest()
            out.append(
                {
                    "source": "feishu",
                    "message_id": mid,
                    "image_key": image_key,
                    "md5": md5,
                    "bytes": data,
                    "file_name": f"feishu-{md5[:8]}.jpg",
                }
            )
    return out
