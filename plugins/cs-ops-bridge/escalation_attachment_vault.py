"""ESC attachment vault — content-addressed blob storage with signed upload tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import cal

_PLUGIN_ROOT = Path(__file__).resolve().parent

ALLOWED_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"})
MAX_BYTES = int(os.environ.get("CS_OPS_ESC_VAULT_MAX_BYTES", str(32 * 1024 * 1024)))
MAX_FILES = int(os.environ.get("CS_OPS_ESC_VAULT_MAX_FILES", "10"))
TOKEN_TTL_SEC = int(os.environ.get("CS_OPS_ESC_VAULT_TOKEN_TTL_SEC", str(7 * 24 * 3600)))


def vault_dir() -> Path:
    raw = os.environ.get("CS_OPS_ESC_VAULT_DIR", "")
    if raw:
        return Path(raw).expanduser().resolve()
    return (_PLUGIN_ROOT / "data" / "esc_vault").resolve()


def public_base_url() -> str:
    explicit = os.environ.get("CS_OPS_ESC_VAULT_PUBLIC_BASE", "").strip()
    if explicit:
        return explicit.rstrip("/")
    from .bridge_lan import default_vault_public_base

    return default_vault_public_base()


from .bridge_secrets import require_bridge_key_bytes


def _bridge_key() -> bytes:
    return require_bridge_key_bytes()


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\- ()\u4e00-\u9fff]", "_", base)
    return base[:200] or "upload.bin"


def _kind_for_ext(ext: str) -> str:
    ext = ext.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return "image"
    return "other"


def issue_upload_token(*, escalation_id: int, issued_at: Optional[int] = None) -> str:
    ts = issued_at if issued_at is not None else int(time.time())
    payload = f"esc:{escalation_id}:{ts}"
    sig = hmac.new(_bridge_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def verify_upload_token(*, escalation_id: int, token: str) -> bool:
    if not token or "." not in token:
        return False
    ts_str, sig = token.split(".", 1)
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if int(time.time()) - ts > TOKEN_TTL_SEC:
        return False
    expected = issue_upload_token(escalation_id=escalation_id, issued_at=ts)
    return hmac.compare_digest(expected, f"{ts}.{sig}")


def build_public_upload_url(*, escalation_id: int) -> str:
    token = issue_upload_token(escalation_id=escalation_id)
    return f"{public_base_url()}/escalations/{escalation_id}/upload?token={token}"


def format_vault_upload_notice(*, escalation_id: int) -> str:
    """Feishu notice block: upload link + SOP (upload before text reply)."""
    url = build_public_upload_url(escalation_id=escalation_id)
    return (
        "\n\n📎 如需上传附件（PDF/图片），请点击：\n"
        f"{url}\n"
        "⚠️ 请务必先上传附件，再在飞书回复文字（先回复后上传的附件无法自动带入草稿）"
    )


def vault_upload_notice_or_fallback(*, escalation_id: int) -> str:
    """Return upload notice text; visible fallback if signing key is unavailable."""
    try:
        return format_vault_upload_notice(escalation_id=escalation_id)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error(
            "vault upload link omitted for esc=%s: %s", escalation_id, exc
        )
        return (
            f"\n\n📎 附件上传链接生成失败（bridge 未配置 HERMES_CS_OPS_BRIDGE_KEY）。"
            f" 请工程执行补发：POST /escalations/{escalation_id}/feishu-upload-link"
        )


def _blob_path(md5: str, ext: str) -> Path:
    root = vault_dir() / "blobs"
    return root / md5[:2] / md5[2:4] / f"{md5}{ext}"


def store_upload(
    *,
    escalation_id: int,
    file_bytes: bytes,
    original_name: str,
    content_type: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> dict[str, Any]:
    """Store file bytes in vault; dedupe by MD5."""
    esc = cal.get_escalation(escalation_id=escalation_id)
    if not esc:
        return {"ok": False, "error": "escalation not found", "status": 404}
    if esc.get("state") not in ("awaiting_answer", "resuming"):
        return {"ok": False, "error": "escalation not accepting uploads", "status": 403}

    existing = cal.list_vault_links_for_escalation(escalation_id=escalation_id)
    if len(existing) >= MAX_FILES:
        return {"ok": False, "error": f"max {MAX_FILES} files per escalation", "status": 422}

    if len(file_bytes) > MAX_BYTES:
        return {"ok": False, "error": f"file exceeds {MAX_BYTES} bytes", "status": 422}

    safe_name = _sanitize_filename(original_name)
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {"ok": False, "error": f"extension not allowed: {ext}", "status": 422}

    md5 = hashlib.md5(file_bytes).hexdigest()
    kind = _kind_for_ext(ext)
    blob = cal.get_vault_blob(md5)
    stored_path = ""

    if blob:
        stored_path = blob["stored_path"]
    else:
        dest = _blob_path(md5, ext)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_bytes)
        rel = str(dest.relative_to(vault_dir() / "blobs"))
        stored_path = rel
        cal.insert_vault_blob(
            md5=md5,
            stored_path=rel,
            size_bytes=len(file_bytes),
            content_type=content_type,
            kind=kind,
        )

    for link in existing:
        if link.get("blob_md5") == md5 and link.get("original_name") == safe_name:
            return {
                "ok": True,
                "deduped": True,
                "link_id": link["id"],
                "blob_md5": md5,
                "original_name": safe_name,
                "kind": kind,
            }

    link_id = str(uuid.uuid4())
    cal.insert_vault_link(
        link_id=link_id,
        escalation_id=escalation_id,
        blob_md5=md5,
        original_name=safe_name,
        uploaded_by=uploaded_by,
    )
    return {
        "ok": True,
        "deduped": bool(blob),
        "link_id": link_id,
        "blob_md5": md5,
        "original_name": safe_name,
        "kind": kind,
        "stored_path": stored_path,
    }


def list_vault_files(*, escalation_id: int) -> list[dict[str, Any]]:
    return cal.list_vault_links_for_escalation(escalation_id=escalation_id)


def resolve_blob_bytes(*, blob_md5: str) -> Optional[bytes]:
    blob = cal.get_vault_blob(blob_md5)
    if not blob:
        return None
    path = vault_dir() / "blobs" / blob["stored_path"]
    if not path.is_file():
        return None
    return path.read_bytes()


def upload_page_html(*, escalation_id: int, token: str, error: str = "") -> str:
    err_block = f'<p style="color:#c00">{error}</p>' if error else ""
    base = public_base_url()
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ESC:{escalation_id} 附件上传</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 2rem auto; padding: 0 1rem; }}
h1 {{ font-size: 1.25rem; }}
.note {{ color: #555; font-size: 0.9rem; }}
input[type=file] {{ margin: 1rem 0; width: 100%; }}
button {{ background: #1677ff; color: #fff; border: none; padding: 0.6rem 1.2rem; border-radius: 6px; cursor: pointer; }}
</style>
</head>
<body>
<h1>升级 ESC:{escalation_id} — 上传附件</h1>
<p class="note">支持 PDF、JPG、PNG 等。<strong>请先完成上传，再在飞书回复文字</strong>（先回复后上传的附件无法自动带入草稿）。</p>
{err_block}
<form method="post" action="{base}/escalations/{escalation_id}/vault?token={token}" enctype="multipart/form-data">
<label>选择文件（最多 {MAX_FILES} 个，单文件 ≤ {MAX_BYTES // (1024*1024)}MB）</label><br/>
<input type="file" name="file" required accept=".pdf,.jpg,.jpeg,.png,.webp,.gif"/>
<br/>
<label>上传者（可选）</label><br/>
<input type="text" name="uploaded_by" placeholder="姓名"/>
<br/><br/>
<button type="submit">上传</button>
</form>
</body>
</html>"""
