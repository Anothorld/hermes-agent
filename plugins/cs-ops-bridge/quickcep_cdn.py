"""Upload local files to QuickCEP CDN via quickcep_cli.

Includes automatic image compression: images >800KB are resized and
recompressed before upload to stay within SMTP attachment delivery limits.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from profile_refs import quickcep_skill_dir

log = logging.getLogger(__name__)

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


def upload_file_to_cdn(
    file_path: Path | str,
    *,
    feature: str = "email",
    timeout: int = 120,
) -> dict[str, Any]:
    """Upload a file and return attachment-ready dict with fileName, fileSize, url."""
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"file not found: {path}"}

    cli = quickcep_skill_dir() / "scripts" / "quickcep_cli.py"
    if not cli.is_file():
        return {"ok": False, "error": f"quickcep_cli not found: {cli}"}

    plugin_root = Path(__file__).resolve().parent
    env = os.environ.copy()
    env.setdefault("CS_OPS_BRIDGE_PLUGIN_DIR", str(plugin_root))

    # Auto-compress images >800KB before upload (SMTP attachment delivery fix)
    upload_path = _compress_image(path)
    is_temp = upload_path != path

    try:
        proc = subprocess.run(
            [sys.executable, str(cli), "upload-file", str(upload_path), "--feature", feature],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cli.parent.parent),
            env=env,
        )
    finally:
        if is_temp:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": proc.stderr or proc.stdout or "upload-file failed",
            "exit_code": proc.returncode,
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid upload-file JSON", "stdout": proc.stdout}

    url = str(data.get("url") or "")
    if not url:
        return {"ok": False, "error": "upload-file returned empty url", "payload": data}

    file_name = str(data.get("fileName") or path.name)
    file_size = int(data.get("fileSize") or path.stat().st_size)
    attachment = {"fileName": file_name, "fileSize": file_size, "url": url}
    return {"ok": True, "url": url, "attachment": attachment, "raw": data}
