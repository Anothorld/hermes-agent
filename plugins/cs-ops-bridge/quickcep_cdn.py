"""Upload local files to QuickCEP CDN.

Performs the multipart upload directly with the stdlib ``urllib`` so it works
under any Python interpreter — including the gateway container's system
``python3`` which has neither ``requests`` nor ``pycryptodome`` installed.
The previous implementation shelled out to ``quickcep_cli.py upload-file``,
which ``import requests`` and therefore failed with ``No module named
'requests'`` whenever the calling interpreter lacked that dependency (root
cause of session 2563708874701037569: QC photos never reached the CDN).

Includes automatic image compression: images >800KB are resized and
recompressed before upload to stay within SMTP attachment delivery limits.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from .profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

# QuickCEP API base + upload endpoint. Mirrors quickcep_cli.BASE.
_QUICKCEP_BASE = "https://app.quickcep.com"
_UPLOAD_PATH = "/robot-configuration/api/file/uploadFile"

# ── Image compression ─────────────────────────────────────────────────────
# SMTP servers silently strip large attachments. The 21MB QC image batch
# (session 2556273723644542979, 5×4.7MB) never reached the customer.
# Target: ≤800KB per image so 5 images = ≤4MB (safe under SMTP limits).

_MAX_UPLOAD_BYTES = 800 * 1024  # 800KB
_RESIZE_MAX_DIM = 1200  # px, longest side
_RESIZE_FALLBACK_DIM = 800  # px, if quality reduction isn't enough
_QUALITY_STEPS = [80, 70, 60, 50]
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _compress_image(path: Path) -> Path:
    """Compress an image to ≤800KB. Returns the path to the compressed file.

    If the original file is already ≤800KB, returns the original path unchanged.
    Otherwise, writes a compressed JPEG to a temp file and returns that path.
    The caller is responsible for cleaning up the temp file.
    """
    original_size = path.stat().st_size
    if original_size <= _MAX_UPLOAD_BYTES:
        return path

    # Non-image files: return as-is (no compression possible)
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        log.info("skip compression: non-image file %s (%d bytes)", path.name, original_size)
        return path

    try:
        from PIL import Image
        # Resampling.LANCZOS = 1 (Pillow >=9.1 uses Image.Resampling.LANCZOS,
        # older versions use Image.LANCZOS, both resolve to the integer 1)
        _LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    except ImportError:
        log.warning("PIL not available — skipping compression for %s (%d bytes)", path.name, original_size)
        return path

    try:
        img = Image.open(path)
        # Convert to RGB if needed (e.g., RGBA PNG → JPEG needs RGB)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size

        # Pass 1: resize to ≤1200px longest side
        if max(w, h) > _RESIZE_MAX_DIM:
            scale = _RESIZE_MAX_DIM / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), _LANCZOS)
            log.info("resized %s from %dx%d to %dx%d", path.name, w, h, img.size[0], img.size[1])

        # Pass 2: progressively reduce JPEG quality until ≤800KB
        for quality in _QUALITY_STEPS:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= _MAX_UPLOAD_BYTES:
                tmp = path.parent / f"{path.stem}_compressed.jpg"
                tmp.write_bytes(buf.getvalue())
                log.info(
                    "compressed %s: %dKB → %dKB (Q%d)",
                    path.name, original_size // 1024, buf.tell() // 1024, quality,
                )
                return tmp

        # Pass 3: if still >800KB after Q50, shrink to 800px and retry
        log.info("still >800KB after Q50, shrinking to %dpx", _RESIZE_FALLBACK_DIM)
        w2, h2 = img.size
        if max(w2, h2) > _RESIZE_FALLBACK_DIM:
            scale = _RESIZE_FALLBACK_DIM / max(w2, h2)
            img = img.resize((int(w2 * scale), int(h2 * scale)), _LANCZOS)

        for quality in _QUALITY_STEPS:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= _MAX_UPLOAD_BYTES:
                tmp = path.parent / f"{path.stem}_compressed.jpg"
                tmp.write_bytes(buf.getvalue())
                log.info(
                    "compressed %s: %dKB → %dKB (800px Q%d)",
                    path.name, original_size // 1024, buf.tell() // 1024, quality,
                )
                return tmp

        # Last resort: save whatever we got at Q50 800px
        tmp = path.parent / f"{path.stem}_compressed.jpg"
        img.save(tmp, format="JPEG", quality=50)
        log.warning(
            "could not get %s under 800KB (best: %dKB) — using Q50 800px",
            path.name, tmp.stat().st_size // 1024,
        )
        return tmp

    except Exception as exc:
        log.warning("image compression failed for %s: %s — using original", path.name, exc)
        return path


def _load_cached_jwt() -> str | None:
    """Return a fresh cached QuickCEP JWT, or ``None`` if none is available.

    Reads ``.quickcep_token.json`` from the quickcep skill scripts dir directly
    (no ``import quickcep_login`` — that module ``import requests`` at import
    time and is unavailable under the gateway's system python3). The bridge
    watcher keeps this token fresh on the shared profile volume, so a valid
    cached token is normally present in both the bridge and gateway
    containers. Auto-login is intentionally NOT attempted here: login itself
    needs ``requests``/``pycryptodome`` and would fail under system python3;
    callers surface the error and retry after the watcher refreshes the token.
    """
    token_file = quickcep_skill_dir() / "scripts" / ".quickcep_token.json"
    try:
        with open(token_file) as f:
            cached = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    jwt = str(cached.get("jwt") or "")
    if not jwt:
        return None
    # 1h safety margin, matching quickcep_cli.get_jwt: a missing/zero expiry
    # is treated as stale (forces a refresh via the bridge watcher).
    try:
        expires_at = float(cached.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= time.time() + 3600:
        log.info("quickcep token expired (expires_at=%s); skipping upload", expires_at)
        return None
    return jwt


def _build_multipart_body(fields: list[tuple[str, tuple[str | None, bytes]]], boundary: str) -> bytes:
    """Build a ``multipart/form-data`` body with stdlib only.

    Each field is ``(name, (filename, payload))``. ``filename`` is ``None`` for
    plain form values. Mirrors the field names the QuickCEP endpoint expects
    (``file``, ``mainModule``, ``subModule``, ``feature``) — same as the prior
    ``requests``-based ``files=`` mapping in ``quickcep_cli.cmd_upload_file``.
    """
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, (filename, payload) in fields:
        part = [f"--{boundary}".encode(), crlf]
        if filename is None:
            part.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        else:
            safe_name = (
                str(filename).replace('"', "_").replace("\r", "").replace("\n", "")
            )
            part.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{safe_name}"'.encode()
            )
            mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
            part.append(f"Content-Type: {mime}".encode())
        part.append(crlf)
        part.append(crlf)
        part.append(payload if isinstance(payload, bytes) else str(payload).encode())
        part.append(crlf)
        parts.append(b"".join(part))
    parts.append(f"--{boundary}--".encode())
    parts.append(crlf)
    return b"".join(parts)


def upload_file_to_cdn(
    file_path: Path | str,
    *,
    feature: str = "email",
    timeout: int = 120,
) -> dict[str, Any]:
    """Upload a file and return an attachment-ready dict (fileName, fileSize, url).

    Uses stdlib ``urllib`` only — no ``requests`` dependency — so it works under
    the gateway container's system ``python3``. Returns ``{"ok": True, ...}``
    on success or ``{"ok": False, "error": ...}`` on failure (never raises).
    """
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}"}

    jwt = _load_cached_jwt()
    if not jwt:
        return {
            "ok": False,
            "error": "no valid QuickCEP token cached; refresh via bridge watcher and retry",
        }

    # Auto-compress images >800KB before upload (SMTP attachment delivery fix).
    upload_path = _compress_image(path)
    is_temp = upload_path != path

    try:
        data = upload_path.read_bytes()
    except OSError as exc:
        if is_temp:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": False, "error": f"could not read {upload_path}: {exc}"}

    # Keep a stable customer-facing name; when we recompressed to JPEG, use .jpg
    # so the multipart filename matches the bytes (not photo.png_compressed.jpg).
    multipart_name = f"{path.stem}.jpg" if is_temp else path.name
    file_name = str(path.name)
    file_size = len(data)  # size of bytes actually uploaded (post-compression)

    boundary = "----quickcep_cdn" + uuid.uuid4().hex
    body = _build_multipart_body(
        [
            ("file", (multipart_name, data)),
            ("mainModule", (None, b"message-center")),
            ("subModule", (None, b"im")),
            ("feature", (None, (feature or "send-image").encode())),
        ],
        boundary,
    )

    headers = {
        "quick-token": jwt,
        "Origin": _QUICKCEP_BASE,
        "Referer": f"{_QUICKCEP_BASE}/panel/conversations",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(
        f"{_QUICKCEP_BASE}{_UPLOAD_PATH}", data=body, headers=headers, method="POST"
    )

    resp_data: Any = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {"ok": False, "error": f"HTTP {exc.code}", "body": err_body}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "error": f"upload request failed: {exc}"}
    finally:
        if is_temp:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not isinstance(resp_data, dict):
        return {"ok": False, "error": "unexpected upload response", "payload": resp_data}
    result_code = resp_data.get("code")
    if result_code is not None and result_code != 200:
        return {
            "ok": False,
            "error": f"upload API code {result_code}",
            "payload": resp_data,
        }
    cdn_url = str(resp_data.get("data") or "")
    if not cdn_url:
        return {"ok": False, "error": "upload returned empty url", "payload": resp_data}

    attachment = {"fileName": file_name, "fileSize": file_size, "url": cdn_url}
    return {
        "ok": True,
        "url": cdn_url,
        "attachment": attachment,
        "raw": {
            "action": "upload_file",
            "fileName": file_name,
            "fileSize": file_size,
            "url": cdn_url,
            "result_code": result_code,
        },
    }
