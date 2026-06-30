"""Minimal Feishu Open API client for cs-ops-bridge (escalation notify + poller)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeishuSendResult:
    ok: bool
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    chat_id: Optional[str] = None
    error: Optional[str] = None


def feishu_credentials_present() -> bool:
    return bool(os.environ.get("FEISHU_APP_ID", "").strip() and os.environ.get("FEISHU_APP_SECRET", "").strip())


def escalation_chat_id() -> Optional[str]:
    explicit = os.environ.get("CS_OPS_FEISHU_ESCALATION_CHAT_ID", "").strip()
    if explicit:
        return explicit
    home = os.environ.get("FEISHU_HOME_CHANNEL", "").strip()
    return home or None


def tenant_access_token() -> Optional[str]:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        log.warning("feishu token HTTP error: %s", exc)
        return None
    except OSError as exc:
        log.warning("feishu token request failed: %s", exc)
        return None
    token = data.get("tenant_access_token")
    if not token:
        log.warning("feishu token missing in response: %s", data.get("msg"))
        return None
    return str(token)


def send_group_text(*, chat_id: str, text: str, token: Optional[str] = None) -> FeishuSendResult:
    """Post a text message to a Feishu group chat."""
    tok = token or tenant_access_token()
    if not tok:
        return FeishuSendResult(ok=False, error="missing FEISHU_APP_ID/FEISHU_APP_SECRET")
    if not chat_id:
        return FeishuSendResult(ok=False, error="missing chat_id")
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    url = "https://open.feishu.cn/open-apis/im/v1/messages?" + urllib.parse.urlencode(
        {"receive_id_type": "chat_id"}
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        log.warning("feishu send failed chat=%s: %s %s", chat_id, exc.code, err[:200])
        return FeishuSendResult(ok=False, chat_id=chat_id, error=f"HTTP {exc.code}: {err[:300]}")
    except OSError as exc:
        return FeishuSendResult(ok=False, chat_id=chat_id, error=str(exc))

    if int(data.get("code", -1)) != 0:
        return FeishuSendResult(
            ok=False,
            chat_id=chat_id,
            error=str(data.get("msg") or data),
        )
    msg_data: dict[str, Any] = data.get("data") or {}
    message_id = str(msg_data.get("message_id") or "") or None
    # Topic groups return omt_* thread_id; normal group posts only have om_* message_id.
    # Pollers must list chat replies by parent_id — om_* is not a valid thread container.
    thread_id = str(msg_data.get("thread_id") or message_id or "") or None
    return FeishuSendResult(
        ok=True,
        message_id=message_id,
        thread_id=thread_id,
        chat_id=chat_id,
    )


def list_container_messages(
    *,
    token: str,
    container_id_type: str,
    container_id: str,
    page_size: int = 50,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """List Feishu IM messages with page_token pagination (newest page first per API)."""
    all_items: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    pages = max(1, max_pages)
    for _ in range(pages):
        params: dict[str, str] = {
            "container_id_type": container_id_type,
            "container_id": container_id,
            "page_size": str(page_size),
        }
        if page_token:
            params["page_token"] = page_token
        url = "https://open.feishu.cn/open-apis/im/v1/messages?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", {}).get("items") or []
        if isinstance(items, list):
            all_items.extend(items)
        page_data = data.get("data") or {}
        if not page_data.get("has_more"):
            break
        page_token = str(page_data.get("page_token") or "") or None
        if not page_token:
            break
    return all_items


def reply_to_message(*, message_id: str, text: str, token: Optional[str] = None) -> FeishuSendResult:
    """Post a threaded reply under an existing group message."""
    tok = token or tenant_access_token()
    if not tok:
        return FeishuSendResult(ok=False, error="missing FEISHU_APP_ID/FEISHU_APP_SECRET")
    if not message_id:
        return FeishuSendResult(ok=False, error="missing message_id")
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        return FeishuSendResult(ok=False, error=f"HTTP {exc.code}: {err[:300]}")
    except OSError as exc:
        return FeishuSendResult(ok=False, error=str(exc))
    if int(data.get("code", -1)) != 0:
        return FeishuSendResult(ok=False, error=str(data.get("msg") or data))
    msg_data: dict[str, Any] = data.get("data") or {}
    mid = str(msg_data.get("message_id") or "") or None
    return FeishuSendResult(ok=True, message_id=mid, chat_id=msg_data.get("chat_id"))


def download_message_resource(
    *,
    message_id: str,
    file_key: str,
    resource_type: str = "image",
    token: Optional[str] = None,
) -> Optional[bytes]:
    """Download binary resource (image/file) from a Feishu message."""
    tok = token or tenant_access_token()
    if not tok or not message_id or not file_key:
        return None
    qs = urllib.parse.urlencode({"type": resource_type})
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {tok}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except (urllib.error.HTTPError, OSError) as exc:
        log.warning("feishu resource download failed msg=%s key=%s: %s", message_id, file_key, exc)
        return None
