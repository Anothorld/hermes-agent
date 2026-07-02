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
body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 640px; margin: 1.5rem auto; padding: 0 1rem; color: #1d1d1f; }}
h1 {{ font-size: 1.2rem; }}
.note {{ color: #555; font-size: 0.85rem; line-height: 1.5; }}
#drop {{ border: 1.5px dashed #c7c7cc; border-radius: 12px; padding: 1.2rem; text-align: center; color: #86868b; margin: 1rem 0; cursor: pointer; transition: border-color .15s, background .15s; }}
#drop.hover {{ border-color: #1677ff; background: #f0f6ff; }}
#drop input {{ display: none; }}
.btn {{ background: #1677ff; color: #fff; border: none; padding: 0.5rem 1.1rem; border-radius: 8px; cursor: pointer; font-size: 0.9rem; }}
.btn:disabled {{ opacity: .5; cursor: not-allowed; }}
.btn.ghost {{ background: #fff; color: #1d1d1f; border: 1px solid #d2d2d7; }}
#list {{ display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }}
.file {{ display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid #e5e5ea; border-radius: 10px; background: #fff; }}
.file .thumb {{ width: 40px; height: 40px; border-radius: 6px; object-fit: cover; background: #f2f2f7; flex: none; }}
.file .thumb.ico {{ display: flex; align-items: center; justify-content: center; font-size: 1.2rem; }}
.file .meta {{ flex: 1; min-width: 0; }}
.file .meta .fn {{ font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.file .meta .sub {{ font-size: 0.72rem; color: #86868b; }}
.file .acts {{ display: flex; gap: 6px; flex: none; }}
.file .acts a, .file .acts button {{ font-size: 0.78rem; }}
.file .rm {{ background: none; border: none; color: #ff3b30; cursor: pointer; padding: 2px 6px; border-radius: 6px; }}
.file .rm:hover {{ background: #fff0f0; }}
.tag {{ font-size: 0.68rem; padding: 1px 6px; border-radius: 4px; background: #e8f0ff; color: #1677ff; }}
.err {{ color: #ff3b30; font-size: 0.8rem; }}
#overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,.6); display: none; align-items: center; justify-content: center; z-index: 999; padding: 24px; }}
#overlay.show {{ display: flex; }}
#overlay .box {{ background: #fff; border-radius: 14px; max-width: 90vw; max-height: 88vh; overflow: auto; position: relative; padding: 12px; }}
#overlay .box img, #overlay .box iframe {{ max-width: 86vw; max-height: 80vh; border: 0; }}
#overlay .close {{ position: absolute; top: 6px; right: 10px; background: none; border: none; font-size: 1.4rem; cursor: pointer; color: #86868b; }}
.spinner {{ width: 16px; height: 16px; border: 2px solid #d2d2d7; border-top-color: #1677ff; border-radius: 50%; animation: sp .7s linear infinite; display: inline-block; vertical-align: middle; }}
@keyframes sp {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<h1>升级 ESC:{escalation_id} — 附件上传</h1>
<p class="note">支持 PDF、JPG、PNG、WEBP、GIF 等。可一次选择<strong>多个文件</strong>，上传后可预览与移除。<strong>请先完成上传，再在飞书回复文字</strong>（先回复后上传的附件无法自动带入草稿）。</p>
{err_block}
<div id="drop">
  <input type="file" id="finput" multiple accept=".pdf,.jpg,.jpeg,.png,.webp,.gif,.doc,.docx,.xls,.xlsx,.txt" />
  点击或拖拽文件到此处上传（最多 {MAX_FILES} 个，单文件 ≤ {MAX_BYTES // (1024*1024)}MB）
</div>
<div id="list"></div>
<div id="overlay"><div class="box"><button class="close" onclick="closeOv()">×</button><div id="ovBody"></div></div></div>
<script>
const ESC={escalation_id}, TOKEN="{token}", BASE="{base}";
function esc(s){{return String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
function kindOf(ct,name){{ct=ct||'';name=(name||'').toLowerCase();if(ct.startsWith('image/')||/\\.(png|jpe?g|webp|gif|bmp)$/.test(name))return'image';if(ct==='application/pdf'||name.endsWith('.pdf'))return'pdf';return'other';}}
function sizeStr(n){{n=+n||0;if(n<1024)return n+' B';if(n<1048576)return(n/1024).toFixed(0)+' KB';return(n/1048576).toFixed(1)+' MB';}}
function contentUrl(id){{return BASE+'/escalations/'+ESC+'/vault/'+encodeURIComponent(id)+'/content?token='+TOKEN;}}
function openPreview(f){{const ov=document.getElementById('overlay'),b=document.getElementById('ovBody');const k=f.kind,u=contentUrl(f.id);if(k==='image'){{b.innerHTML='<img src="'+esc(u)+'" />';}}else if(k==='pdf'){{b.innerHTML='<iframe src="'+esc(u)+'"></iframe>';}}else{{b.innerHTML='<a href="'+esc(u)+'" target="_blank">此文件类型无法在线预览，点此下载</a>';}}ov.classList.add('show');}}
function closeOv(){{document.getElementById('overlay').classList.remove('show');document.getElementById('ovBody').innerHTML='';}}
document.getElementById('overlay').addEventListener('click',e=>{{if(e.target.id==='overlay')closeOv();}});
async function load(){{try{{const r=await fetch(BASE+'/escalations/'+ESC+'/vault?token='+TOKEN);const j=await r.json();render(j.files||[]);}}catch(e){{}}}}
let CUR=[];
const LIST_EL=document.getElementById('list');
function render(files){{CUR=files;if(!files.length){{LIST_EL.innerHTML='<p class="note">尚未上传附件</p>';return;}}LIST_EL.innerHTML=files.map((f,i)=>{{const k=f.kind||kindOf(f.content_type,f.original_name);const thumb=k==='image'?'<img class="thumb" src="'+esc(contentUrl(f.id))+'"/>':'<div class="thumb ico">'+(k==='pdf'?'📄':'📎')+'</div>';return '<div class="file" data-idx="'+i+'">'+thumb+'<div class="meta"><div class="fn">'+esc(f.original_name||'文件')+'</div><div class="sub">'+sizeStr(f.size_bytes)+' · '+(f.uploaded_by||'—')+' · <span class="tag">'+k+'</span></div></div><div class="acts"><button class="btn ghost" data-act="prev">预览</button><button class="rm" data-act="del">移除</button></div></div>';}}).join('');}}
LIST_EL.addEventListener('click',function(e){{const row=e.target.closest('.file');if(!row)return;const f=CUR[+row.dataset.idx];if(!f)return;if(e.target.dataset.act==='prev')openPreview(f);else if(e.target.dataset.act==='del')del(f.id);}});
async function del(id){{if(!confirm('确认移除该附件？'))return;try{{await fetch(BASE+'/escalations/'+ESC+'/vault/'+encodeURIComponent(id)+'?token='+TOKEN,{{method:'DELETE'}});await load();}}catch(e){{alert('移除失败');}}}}
const drop=document.getElementById('drop'),finput=document.getElementById('finput');
drop.addEventListener('click',()=>finput.click());
drop.addEventListener('dragover',e=>{{e.preventDefault();drop.classList.add('hover');}});
drop.addEventListener('dragleave',()=>drop.classList.remove('hover'));
drop.addEventListener('drop',e=>{{e.preventDefault();drop.classList.remove('hover');if(e.dataTransfer.files)upload(e.dataTransfer.files);}});
finput.addEventListener('change',()=>{{if(finput.files)upload(finput.files);finput.value='';}});
async function upload(files){{for(const file of Array.from(files)){{const fd=new FormData();fd.append('file',file);const row=document.createElement('div');row.className='file';row.innerHTML='<div class="thumb ico"><span class="spinner"></span></div><div class="meta"><div class="fn">'+esc(file.name)+'</div><div class="sub sub">上传中…</div></div>';document.getElementById('list').appendChild(row);try{{const r=await fetch(BASE+'/escalations/'+ESC+'/vault?token='+TOKEN,{{method:'POST',body:fd}});const j=await r.json();if(!r.ok)throw new Error(j.detail||'上传失败');row.querySelector('.sub').textContent='已上传 ✓';}}catch(e){{row.querySelector('.sub').innerHTML='<span class="err">'+esc(e.message)+'</span>';}}}}await load();}}
load();
</script>
</body>
</html>"""
