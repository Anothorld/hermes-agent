"""Reel video download — yt-dlp subprocess + cookies from local-chrome profile.

Downloads IG Reel MP4 files for content evaluation (video mode only).
Uses cookies exported from the local-chrome-debug-profile to authenticate.
Auto-cleans files older than 1 hour; disk cap prevents unbounded growth.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

_HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
_CHROME_PROFILE = os.environ.get(
    "DEBUG_CHROME_PROFILE_DIR",
    os.path.join(_HERMES_HOME, "local-chrome-debug-profile"),
)
_DEFAULT_DEST = os.path.join(_HERMES_HOME, "kol-rpa-reels")
_COOKIE_DB = os.path.join(_CHROME_PROFILE, "Default", "Cookies")
_IG_COOKIE_DOMAIN = ".instagram.com"
_DISK_CAP_MB = float(os.environ.get("KOL_RPA_REEL_DISK_CAP_MB", "2000"))
_MAX_AGE_HOURS = float(os.environ.get("KOL_RPA_REEL_MAX_AGE_HOURS", "1"))


def _get_chrome_safe_storage_key() -> bytes | None:
    """Fetch the Chrome Safe Storage password from the macOS Keychain.

    Returns ``None`` on non-macOS or keychain access failure (callers fall back
    to no-cookie / public-reel download). Best-effort — may surface a keychain
    authorization prompt the first time.
    """
    import sys

    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip().encode("utf-8")
    except Exception:
        return None


def _derive_chrome_aes_key(password: bytes) -> bytes:
    """Derive the AES key Chrome uses to encrypt cookie values (macOS)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    return kdf.derive(password)


def _decrypt_chrome_cookie(encrypted_value: bytes, aes_key: bytes) -> str:
    """Decrypt a Chrome cookie ``encrypted_value`` BLOB (macOS v10 scheme).

    Chrome macOS encrypts cookie values with AES-128-CBC using a fixed IV of 16
    space bytes and a key derived from the Keychain "Chrome Safe Storage"
    password. The BLOB is ``b"v10" + ciphertext``.
    """
    if not encrypted_value or not encrypted_value.startswith(b"v10"):
        return ""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    ciphertext = encrypted_value[3:]  # strip "v10" version prefix
    iv = b" " * 16  # Chrome uses a fixed IV of 16 spaces on macOS
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    # Strip PKCS7 padding
    if padded:
        pad_len = padded[-1]
        if 1 <= pad_len <= 16 and padded[-pad_len:] == bytes([pad_len]) * pad_len:
            padded = padded[:-pad_len]
    return padded.decode("utf-8", errors="replace")


def _export_cookies() -> str:
    """Export IG cookies from Chrome profile to Netscape cookies.txt format.

    Chrome stores cookie VALUES encrypted in the ``encrypted_value`` column (the
    ``value`` column is empty for encrypted cookies). On macOS this decrypts them
    with the Keychain "Chrome Safe Storage" key so yt-dlp gets real session
    cookies; without this, yt-dlp saw empty values and warned "cookies are no
    longer valid", blocking private/age-gated reels. Public reels download fine
    without cookies, so decryption failure degrades gracefully (empty values are
    still written; yt-dlp will simply not authenticate).

    Returns:
        Path to temporary cookies.txt file.

    Raises:
        DownloadError: If cookie DB not found or unreadable.
        CookieExpiredError: If sessionid cookie is missing entirely.
    """
    from errors import CookieExpiredError, DownloadError

    db_path = Path(_COOKIE_DB)
    if not db_path.exists():
        raise DownloadError("cookie_db_not_found", f"Chrome cookie DB not found: {db_path}")

    # Copy to temp for read-only access (Chrome locks the DB)
    tmp_db = Path(tempfile.gettempdir()) / f"chrome_cookies_{os.getpid()}.db"
    try:
        shutil.copy2(db_path, tmp_db)
        conn = sqlite3.connect(str(tmp_db))
        cursor = conn.execute(
            "SELECT name, value, encrypted_value, host_key, path, expires_utc, "
            "is_secure, is_httponly, samesite FROM cookies WHERE host_key = ?",
            (_IG_COOKIE_DOMAIN,),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        raise DownloadError("cookie_read_error", str(e))
    finally:
        try:
            tmp_db.unlink(missing_ok=True)
        except Exception:
            pass

    # Check for sessionid presence (by name) before bothering with decryption.
    if not any(row[0] == "sessionid" for row in rows):
        raise CookieExpiredError()

    # Best-effort Chrome cookie decryption (macOS). Falls back to the plaintext
    # `value` column on other platforms / when the keychain is unavailable.
    aes_key = None
    storage_key = _get_chrome_safe_storage_key()
    if storage_key:
        try:
            aes_key = _derive_chrome_aes_key(storage_key)
        except Exception:
            aes_key = None

    # Write Netscape format cookies.txt
    cookies_path = Path(tempfile.gettempdir()) / f"ig_cookies_{os.getpid()}.txt"
    with open(cookies_path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for name, value, encrypted_value, host_key, path, expires_utc, is_secure, _is_http, _samesite in rows:
            # Prefer plaintext value; fall back to decrypting encrypted_value.
            cookie_value = value or ""
            if not cookie_value and encrypted_value and aes_key:
                try:
                    cookie_value = _decrypt_chrome_cookie(encrypted_value, aes_key)
                except Exception:
                    cookie_value = ""
            if expires_utc > 0:
                unix_expires = int(expires_utc / 1_000_000 - 11644473600)
            else:
                unix_expires = 0
            secure_str = "TRUE" if is_secure else "FALSE"
            # Netscape format: domain\tflag\tpath\tsecure\texpiration\tname\tvalue
            f.write(f"{host_key}\tTRUE\t{path}\t{secure_str}\t{unix_expires}\t{name}\t{cookie_value}\n")

    return str(cookies_path)


def _cookies_look_valid(cookies_path: str) -> bool:
    """Check whether the exported cookies.txt has a usable (printable) sessionid.

    Chrome encrypts cookie values; on macOS the decryption is best-effort and
    version-dependent. If the sessionid value isn't printable ASCII, the cookies
    are unusable — callers skip ``--cookies`` and download as a public client
    (which works for public reels; only private/age-gated reels need auth).
    """
    try:
        with open(cookies_path) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and parts[5] == "sessionid":
                    val = parts[6]
                    return bool(val) and all(32 <= ord(c) < 127 for c in val)
        return False
    except Exception:
        return False


def _cleanup_expired(dest_dir: Path, max_age_hours: float = _MAX_AGE_HOURS) -> int:
    """Delete reel video + cover image files older than max_age_hours. Returns count deleted."""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for pattern in ("*.mp4", "*.jpg", "*.jpeg", "*.webp", "*.png"):
        for f in dest_dir.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
    return removed


def _dir_size_mb(dest_dir: Path) -> float:
    return sum(
        f.stat().st_size
        for pattern in ("*.mp4", "*.jpg", "*.jpeg", "*.webp", "*.png")
        for f in dest_dir.glob(pattern)
    ) / 1_000_000


def _reel_id_from_url(reel_url: str) -> str:
    """Extract the reel shortcode from an IG reel URL."""
    import re

    m = re.search(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)", reel_url or "")
    return m.group(1) if m else "unknown"


def _find_file_for_reel(dest: Path, reel_id: str, exts: tuple[str, ...]) -> str:
    """Locate the downloaded file for a reel id by extension. Returns '' if not found."""
    if not reel_id or reel_id == "unknown":
        return ""
    for ext in exts:
        for f in dest.glob(f"{reel_id}.{ext}"):
            return str(f)
        for f in dest.glob(f"{reel_id}*.{ext}"):
            return str(f)
    return ""


def _run_yt_dlp(cmd: list[str], timeout: int) -> "subprocess.CompletedProcess[str]":
    """Run yt-dlp, raising clean DownloadErrors for common failure modes."""
    from errors import DownloadError

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
    except FileNotFoundError:
        raise DownloadError(
            "yt_dlp_not_found",
            "yt-dlp is not installed or not on PATH. Install it (pip install yt-dlp) "
            "to enable reel/cover downloads.",
        )
    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if "404" in stderr or "empty media response" in stderr:
            raise DownloadError(
                "yt_dlp_ig_extractor_failed",
                "yt-dlp's Instagram extractor failed (HTTP 404 / empty media). "
                "This is an upstream yt-dlp issue — IG changed its media API. "
                "Update yt-dlp (yt-dlp -U) or use a working fork. Cookies were "
                f"exported correctly. yt-dlp stderr: {stderr[:300]}",
            )
        raise DownloadError("download_error", f"yt-dlp exit {proc.returncode}: {stderr[:500]}")
    return proc


def _parse_thumbnail_from_stdout(stdout: str) -> str:
    """Extract the thumbnail URL from yt-dlp --print thumbnail output."""
    for line in (stdout or "").strip().splitlines():
        line = line.strip()
        if line.startswith("http") and ("cdninstagram" in line or "scontent" in line or "fbcdn" in line):
            return line
    return ""


def download_reel(
    reel_url: str,
    dest_dir: str | None = None,
    quality: str = "best",
    timeout: int = 120,
    write_thumbnail: bool = True,
) -> dict:
    """Download an IG Reel as MP4 (and its cover image) via yt-dlp.

    Uses ``--print thumbnail`` + a dest-dir glob to locate the saved file —
    ``--dump-json`` silently simulates (no file write) in yt-dlp >= 2026, so it
    cannot be used to both fetch metadata and download. ``--write-thumbnail``
    saves the cover alongside the video for ``vision_analyze``.

    Args:
        reel_url: IG Reel URL.
        dest_dir: Destination directory (default ~/.hermes/kol-rpa-reels/).
        quality: yt-dlp quality selector. ``"best"`` (default) omits ``-f`` so
            yt-dlp picks the best muxed format (auto-falls-back when ffmpeg is
            absent); pass a specific selector to override.
        timeout: Subprocess timeout in seconds.
        write_thumbnail: Also download the cover image (default True).

    Returns:
        Dict with file_path, file_size_bytes, duration_s, thumbnail_url,
        cover_path (local cover image, when write_thumbnail).

    Raises:
        DownloadError: If yt-dlp fails, disk cap exceeded, or file not written.
        CookieExpiredError: If sessionid cookie is missing.
    """
    from errors import DownloadError

    dest = Path(dest_dir or _DEFAULT_DEST)
    dest.mkdir(parents=True, exist_ok=True)
    _cleanup_expired(dest)
    if _dir_size_mb(dest) > _DISK_CAP_MB:
        raise DownloadError("disk_cap_exceeded", f"disk usage exceeds {_DISK_CAP_MB}MB cap")

    reel_id = _reel_id_from_url(reel_url)
    cookies_path = _export_cookies()
    use_cookies = _cookies_look_valid(cookies_path)
    try:
        cmd = [
            "yt-dlp", "--no-update", "--no-simulate",
            "-o", str(dest / "%(id)s.%(ext)s"),
        ]
        if use_cookies:
            cmd += ["--cookies", cookies_path]
        if quality and quality != "best":
            cmd += ["-f", quality]
        if write_thumbnail:
            cmd += ["--write-thumbnail"]
        cmd += ["--print", "thumbnail", reel_url]

        proc = _run_yt_dlp(cmd, timeout)
        thumbnail_url = _parse_thumbnail_from_stdout(proc.stdout)

        video_path = _find_file_for_reel(dest, reel_id, ("mp4", "webm", "mkv"))
        cover_path = _find_file_for_reel(dest, reel_id, ("jpg", "jpeg", "webp", "png")) if write_thumbnail else ""

        if not video_path or not os.path.exists(video_path):
            raise DownloadError(
                "download_error",
                f"yt-dlp reported success but no video file for reel {reel_id} in {dest}. "
                f"stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}",
            )
        return {
            "file_path": video_path,
            "file_size_bytes": os.path.getsize(video_path),
            "duration_s": None,  # yt-dlp's IG extractor does not populate duration
            "thumbnail_url": thumbnail_url,
            "cover_path": cover_path,
            "reel_id": reel_id,
        }
    except subprocess.TimeoutExpired:
        raise DownloadError("download_timeout", f"yt-dlp timed out after {timeout}s")
    finally:
        try:
            os.unlink(cookies_path)
        except Exception:
            pass


def download_cover(
    reel_url: str,
    dest_dir: str | None = None,
    timeout: int = 60,
) -> dict:
    """Download ONLY an IG Reel's cover image (no video) via yt-dlp.

    For cover-mode content screening (``KOL_RPA_VIDEO_EVAL_ENABLED=0``): the
    agent calls this per reel to fetch a cover image file for ``vision_analyze``,
    without downloading the video. Uses ``--write-thumbnail --skip-download`` so
    only the cover is fetched (fast, no ffmpeg needed).

    Args:
        reel_url: IG Reel URL.
        dest_dir: Destination directory (default ~/.hermes/kol-rpa-reels/).
        timeout: Subprocess timeout in seconds.

    Returns:
        Dict with cover_path, file_size_bytes, thumbnail_url, reel_id.

    Raises:
        DownloadError: If yt-dlp fails or the cover file isn't written.
        CookieExpiredError: If sessionid cookie is missing.
    """
    from errors import DownloadError

    dest = Path(dest_dir or _DEFAULT_DEST)
    dest.mkdir(parents=True, exist_ok=True)
    _cleanup_expired(dest)
    if _dir_size_mb(dest) > _DISK_CAP_MB:
        raise DownloadError("disk_cap_exceeded", f"disk usage exceeds {_DISK_CAP_MB}MB cap")

    reel_id = _reel_id_from_url(reel_url)
    cookies_path = _export_cookies()
    use_cookies = _cookies_look_valid(cookies_path)
    try:
        cmd = [
            "yt-dlp", "--no-update", "--no-simulate",
            "--skip-download", "--write-thumbnail",
            "-o", str(dest / "%(id)s.%(ext)s"),
            "--print", "thumbnail", reel_url,
        ]
        if use_cookies:
            cmd += ["--cookies", cookies_path]
        proc = _run_yt_dlp(cmd, timeout)
        thumbnail_url = _parse_thumbnail_from_stdout(proc.stdout)
        cover_path = _find_file_for_reel(dest, reel_id, ("jpg", "jpeg", "webp", "png"))

        if not cover_path or not os.path.exists(cover_path):
            raise DownloadError(
                "cover_not_written",
                f"yt-dlp reported success but no cover image for reel {reel_id} in {dest}. "
                f"stdout={proc.stdout[:200]} stderr={proc.stderr[:200]}",
            )
        return {
            "cover_path": cover_path,
            "file_size_bytes": os.path.getsize(cover_path),
            "thumbnail_url": thumbnail_url,
            "reel_id": reel_id,
        }
    except subprocess.TimeoutExpired:
        raise DownloadError("download_timeout", f"yt-dlp timed out after {timeout}s")
    finally:
        try:
            os.unlink(cookies_path)
        except Exception:
            pass


def cleanup_reels(dest_dir: str | None = None, older_than_hours: float = 1.0) -> dict:
    """Delete old reel video + cover image files from the download directory.

    Cleans ``.mp4`` videos and ``.jpg/.jpeg/.webp/.png`` cover images older than
    ``older_than_hours``.

    Args:
        dest_dir: Directory to clean (default ~/.hermes/kol-rpa-reels/).
        older_than_hours: Delete files older than this many hours.

    Returns:
        Dict with deleted_count and freed_mb.
    """
    dest = Path(dest_dir or _DEFAULT_DEST)
    if not dest.exists():
        return {"deleted_count": 0, "freed_mb": 0.0}

    cutoff = time.time() - older_than_hours * 3600
    deleted = 0
    freed_bytes = 0
    for pattern in ("*.mp4", "*.jpg", "*.jpeg", "*.webp", "*.png"):
        for f in dest.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    freed_bytes += f.stat().st_size
                    f.unlink(missing_ok=True)
                    deleted += 1
            except Exception:
                pass

    return {
        "deleted_count": deleted,
        "freed_mb": round(freed_bytes / 1_000_000, 2),
    }
