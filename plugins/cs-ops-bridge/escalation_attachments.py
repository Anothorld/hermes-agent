"""Prepare operator attachments on escalation claim — vault + Feishu images → QuickCEP CDN."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from . import cal
from .escalation_attachment_vault import resolve_blob_bytes, vault_dir
from .feishu_attachment_extract import extract_feishu_thread_images
from .quickcep_cdn import upload_file_to_cdn

log = logging.getLogger(__name__)

UPLOAD_BUDGET_SEC = float(os.environ.get("CS_OPS_CLAIM_UPLOAD_BUDGET_SEC", "60"))
FILE_UPLOAD_TIMEOUT = int(os.environ.get("CS_OPS_VAULT_CDN_UPLOAD_TIMEOUT_SEC", "45"))


def _write_temp_file(*, data: bytes, name: str) -> Path:
    tmp = vault_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    safe = Path(name).name or "upload.bin"
    path = tmp / f"{hashlib.md5(data).hexdigest()[:12]}-{safe}"
    path.write_bytes(data)
    return path


def _cdn_upload_bytes(*, data: bytes, file_name: str) -> Optional[dict[str, Any]]:
    path = _write_temp_file(data=data, name=file_name)
    try:
        result = upload_file_to_cdn(path, feature="email", timeout=FILE_UPLOAD_TIMEOUT)
        if result.get("ok"):
            return result.get("attachment")
        log.warning("CDN upload failed for %s: %s", file_name, result.get("error"))
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _ensure_blob_cdn(*, blob_md5: str, file_name: str) -> Optional[str]:
    blob = cal.get_vault_blob(blob_md5)
    if not blob:
        return None
    if blob.get("cdn_url"):
        return str(blob["cdn_url"])
    data = resolve_blob_bytes(blob_md5=blob_md5)
    if not data:
        return None
    att = _cdn_upload_bytes(data=data, file_name=file_name)
    if not att:
        return None
    url = str(att.get("url") or "")
    if url:
        cal.set_vault_blob_cdn_url(md5=blob_md5, cdn_url=url)
    return url or None


def prepare_escalation_attachments(
    *,
    escalation_id: int,
    feishu_messages: Optional[list[dict[str, Any]]] = None,
    feishu_token: Optional[str] = None,
    exclude_message_ids: Optional[set[str]] = None,
    after_ms: int = 0,
) -> dict[str, Any]:
    """Upload vault + Feishu images to CDN; merge into escalation resume_context."""
    started = time.monotonic()
    esc_row = cal.get_escalation(escalation_id=escalation_id) or {}
    prev_ctx = esc_row.get("resume_context") or {}
    prev_attachments = list(prev_ctx.get("operator_attachments") or [])
    prev_allowed = list(prev_ctx.get("allowed_attachment_urls") or [])
    prev_link_ids = list(prev_ctx.get("vault_link_ids") or [])

    operator_attachments: list[dict[str, Any]] = []
    allowed_urls: list[str] = []
    vault_link_ids: list[str] = []
    seen_md5: set[str] = set()

    def _budget_exceeded() -> bool:
        return (time.monotonic() - started) >= UPLOAD_BUDGET_SEC

    for link in cal.list_vault_links_for_escalation(escalation_id=escalation_id):
        if _budget_exceeded():
            log.warning("claim upload budget exceeded esc=%s", escalation_id)
            break
        md5 = str(link.get("blob_md5") or "")
        if not md5 or md5 in seen_md5:
            vault_link_ids.append(str(link["id"]))
            continue
        name = str(link.get("original_name") or "attachment")
        kind = str(link.get("kind") or "other")
        url = _ensure_blob_cdn(blob_md5=md5, file_name=name)
        if not url:
            continue
        seen_md5.add(md5)
        vault_link_ids.append(str(link["id"]))
        att = {
            "name": name,
            "fileName": name,
            "url": url,
            "source": "vault",
            "blob_md5": md5,
            "kind": kind,
        }
        if link.get("size_bytes"):
            att["fileSize"] = int(link["size_bytes"])
        operator_attachments.append(att)
        allowed_urls.append(url)

    if feishu_messages and feishu_token and not _budget_exceeded():
        feishu_items = extract_feishu_thread_images(
            feishu_messages,
            token=feishu_token,
            after_ms=after_ms,
            exclude_message_ids=exclude_message_ids,
        )
        for item in feishu_items:
            if _budget_exceeded():
                break
            md5 = str(item.get("md5") or "")
            if md5 in seen_md5:
                continue
            att_obj = _cdn_upload_bytes(data=item["bytes"], file_name=str(item.get("file_name") or "feishu.jpg"))
            if not att_obj:
                continue
            seen_md5.add(md5)
            url = str(att_obj.get("url") or "")
            operator_attachments.append(
                {
                    "name": att_obj.get("fileName"),
                    "fileName": att_obj.get("fileName"),
                    "fileSize": att_obj.get("fileSize"),
                    "url": url,
                    "source": "feishu",
                    "blob_md5": md5,
                    "kind": "image",
                }
            )
            allowed_urls.append(url)

    if not operator_attachments and prev_attachments:
        log.warning(
            "prepare produced no attachments; preserving previous resume_context esc=%s",
            escalation_id,
        )
        operator_attachments = prev_attachments
        allowed_urls = prev_allowed
        vault_link_ids = prev_link_ids

    patch: dict[str, Any] = {
        "operator_attachments": operator_attachments,
        "allowed_attachment_urls": allowed_urls,
        "vault_link_ids": vault_link_ids,
    }
    cal.merge_escalation_resume_context(escalation_id=escalation_id, patch=patch)
    return {
        "ok": True,
        "operator_attachments": operator_attachments,
        "allowed_attachment_urls": allowed_urls,
        "count": len(operator_attachments),
    }
