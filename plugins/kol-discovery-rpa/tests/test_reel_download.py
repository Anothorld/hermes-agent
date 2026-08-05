"""Tests for reel_download — regression guards for the cookie SQL + yt-dlp invocation fixes.

These cover bugs found during real-page verification:
- _export_cookies queried a non-existent `domain` column (Chrome uses `host_key`).
- download_reel didn't catch FileNotFoundError when yt-dlp isn't installed.
- download_reel used `--print-json` (removed in yt-dlp 2025.12).
- yt-dlp IG 404 surfaces a distinct `yt_dlp_ig_extractor_failed` error code.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_PLUGIN_INTERNAL = str(Path(__file__).resolve().parents[1] / "internal")
if _PLUGIN_INTERNAL not in sys.path:
    sys.path.insert(0, _PLUGIN_INTERNAL)


# --------------------------------------------------------------- cookie export

def _make_fake_cookie_db(tmp_path: Path) -> Path:
    """Create a SQLite DB mimicking Chrome's Cookies schema (host_key, no domain)."""
    db = tmp_path / "Cookies"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE cookies ("
        "host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT, "
        "expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER, samesite INTEGER)"
    )
    conn.execute(
        "INSERT INTO cookies (host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite) "
        "VALUES ('.instagram.com', 'sessionid', 'sess123', NULL, '/', 99999999900000000, 1, 1, 0)"
    )
    conn.execute(
        "INSERT INTO cookies (host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite) "
        "VALUES ('.instagram.com', 'csrftoken', 'csrfxyz', NULL, '/', 99999999900000000, 1, 0, 0)"
    )
    conn.commit()
    conn.close()
    return db


def test_export_cookies_uses_host_key_not_domain(tmp_path, monkeypatch):
    """Cookie export must query host_key (Chrome has no `domain` column).

    Regression: the old SQL `SELECT ... domain ... FROM cookies` raised
    "no such column: domain" and broke every reel download.
    """
    import reel_download

    db = _make_fake_cookie_db(tmp_path)
    monkeypatch.setattr(reel_download, "_COOKIE_DB", str(db))

    cookies_path = reel_download._export_cookies()
    assert os.path.exists(cookies_path)
    content = Path(cookies_path).read_text()
    # Netscape format line for sessionid must contain the host_key domain
    assert ".instagram.com" in content
    assert "sessionid" in content
    assert "sess123" in content
    os.unlink(cookies_path)


def test_export_cookies_raises_cookie_expired_when_no_sessionid(tmp_path, monkeypatch):
    """Missing sessionid → CookieExpiredError (not a generic crash)."""
    import reel_download
    from errors import CookieExpiredError

    db = tmp_path / "Cookies"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT, "
        "expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER, samesite INTEGER)"
    )
    conn.execute(
        "INSERT INTO cookies (host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite) "
        "VALUES ('.instagram.com', 'csrftoken', 'xyz', NULL, '/', 0, 1, 0, 0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(reel_download, "_COOKIE_DB", str(db))

    with pytest.raises(CookieExpiredError):
        reel_download._export_cookies()


# --------------------------------------------------------------- yt-dlp invocation

def test_download_reel_missing_yt_dlp_gives_clean_error(monkeypatch):
    """FileNotFoundError from subprocess (yt-dlp not installed) → DownloadError, not a crash."""
    import reel_download
    from errors import DownloadError

    def fake_export():
        return "/tmp/fake_cookies.txt"

    def fake_unlink(path):
        pass

    def fake_run(*a, **kw):
        raise FileNotFoundError("yt-dlp not found")

    monkeypatch.setattr(reel_download, "_export_cookies", fake_export)
    monkeypatch.setattr(reel_download.subprocess, "run", fake_run)

    with pytest.raises(DownloadError) as exc:
        reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir="/tmp")
    assert exc.value.code == "yt_dlp_not_found"


def test_download_reel_uses_print_thumbnail_not_dump_json(monkeypatch, tmp_path):
    """download_reel uses `--print thumbnail` + dest glob (not --dump-json, which
    silently simulates / no file write in yt-dlp >= 2026) and --write-thumbnail
    to also save the cover."""
    import reel_download

    captured = {}

    def fake_export():
        return "/tmp/fake_cookies.txt"

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd

        class _P:
            returncode = 0
            stdout = "https://scontent-lax3-1.cdninstagram.com/v/x.jpg\n"
            stderr = ""
        return _P()

    monkeypatch.setattr(reel_download, "_export_cookies", fake_export)
    monkeypatch.setattr(reel_download.subprocess, "run", fake_run)
    # The glob must find a video file; create one at the expected path.
    video = tmp_path / "abc.mp4"
    video.write_bytes(b"x" * 100)
    monkeypatch.setattr(reel_download, "_find_file_for_reel",
                        lambda dest, rid, exts: str(video) if "mp4" in exts else "")

    r = reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir=str(tmp_path))
    assert "--print" in captured["cmd"]
    assert "thumbnail" in captured["cmd"]
    assert "--no-simulate" in captured["cmd"]
    assert "--dump-json" not in captured["cmd"]
    assert "--print-json" not in captured["cmd"]
    assert "--write-thumbnail" in captured["cmd"]
    assert r["file_path"] == str(video)
    assert r["file_size_bytes"] == 100
    assert r["thumbnail_url"].startswith("https://scontent")


def test_download_reel_omits_f_for_best_quality(monkeypatch, tmp_path):
    """quality='best' (default) omits -f so yt-dlp picks best muxed (auto-fallback
    when ffmpeg absent); a specific quality is passed through."""
    import reel_download

    captured = {}

    def fake_run(cmd, **kw):
        captured.setdefault("cmds", []).append(cmd)

        class _P:
            returncode = 0
            stdout = "https://scontent-x.cdninstagram.com/c.jpg\n"
            stderr = ""
        return _P()

    video = tmp_path / "abc.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(reel_download, "_export_cookies", lambda: "/tmp/fake.txt")
    monkeypatch.setattr(reel_download.subprocess, "run", fake_run)
    monkeypatch.setattr(reel_download, "_find_file_for_reel",
                        lambda dest, rid, exts: str(video) if "mp4" in exts else "")

    reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir=str(tmp_path), quality="best")
    assert "-f" not in captured["cmds"][0]  # best → no -f

    reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir=str(tmp_path), quality="bestvideo")
    assert "-f" in captured["cmds"][1]
    assert "bestvideo" in captured["cmds"][1]


def test_download_reel_raises_when_file_not_written(monkeypatch, tmp_path):
    """yt-dlp returns success but the file isn't on disk → clear DownloadError."""
    import reel_download
    from errors import DownloadError

    class _P:
        returncode = 0
        stdout = "https://scontent-x.cdninstagram.com/c.jpg\n"
        stderr = ""

    monkeypatch.setattr(reel_download, "_export_cookies", lambda: "/tmp/fake.txt")
    monkeypatch.setattr(reel_download.subprocess, "run", lambda *a, **k: _P())
    # _find_file_for_reel returns "" (no file)
    monkeypatch.setattr(reel_download, "_find_file_for_reel", lambda dest, rid, exts: "")

    with pytest.raises(DownloadError) as exc:
        reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir=str(tmp_path))
    assert exc.value.code == "download_error"


def test_download_cover_uses_skip_download_and_write_thumbnail(monkeypatch, tmp_path):
    """download_cover fetches ONLY the cover (--skip-download --write-thumbnail),
    not the video — for cover-mode screening."""
    import reel_download

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd

        class _P:
            returncode = 0
            stdout = "https://scontent-x.cdninstagram.com/c.jpg\n"
            stderr = ""
        return _P()

    cover = tmp_path / "abc.jpg"
    cover.write_bytes(b"img" * 50)
    monkeypatch.setattr(reel_download, "_export_cookies", lambda: "/tmp/fake.txt")
    monkeypatch.setattr(reel_download.subprocess, "run", fake_run)
    monkeypatch.setattr(reel_download, "_find_file_for_reel",
                        lambda dest, rid, exts: str(cover) if exts and exts[0] != "mp4" else "")

    r = reel_download.download_cover("https://www.instagram.com/reel/abc/", dest_dir=str(tmp_path))
    assert "--skip-download" in captured["cmd"]
    assert "--write-thumbnail" in captured["cmd"]
    assert "--no-simulate" in captured["cmd"]
    assert r["cover_path"] == str(cover)
    assert r["file_size_bytes"] == 150
    assert r["thumbnail_url"].startswith("https://scontent")


def test_download_cover_prefers_rpa_thumbnail(monkeypatch, tmp_path):
    """When thumbnail_url is provided, download_cover uses HTTP fetch first."""
    import reel_download

    called = {}

    def fake_thumb(url, reel_id, **kw):
        called["url"] = url
        out = tmp_path / f"{reel_id}.jpg"
        out.write_bytes(b"x" * 20)
        return {
            "cover_path": str(out),
            "file_size_bytes": 20,
            "thumbnail_url": url,
            "reel_id": reel_id,
            "source": "rpa_thumbnail",
        }

    monkeypatch.setattr(reel_download, "download_cover_from_thumbnail", fake_thumb)
    monkeypatch.setattr(reel_download.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("yt-dlp should not run")))

    r = reel_download.download_cover(
        "https://www.instagram.com/reel/abc/",
        dest_dir=str(tmp_path),
        thumbnail_url="https://cdn.example/abc.jpg",
    )
    assert called["url"] == "https://cdn.example/abc.jpg"
    assert r["source"] == "rpa_thumbnail"


def test_download_content_eval_cover_only(monkeypatch, tmp_path):
    """download_content_eval downloads all cover_reels; skips videos in cover mode."""
    import eval_mode
    import reel_download

    monkeypatch.setattr(eval_mode, "resolve_eval_mode", lambda brief=None: "cover")

    def fake_cover(reel_url, **kw):
        rid = reel_download._reel_id_from_url(reel_url)
        path = tmp_path / f"{rid}.jpg"
        path.write_bytes(b"c")
        return {"cover_path": str(path), "file_size_bytes": 1, "reel_id": rid, "source": "rpa_thumbnail"}

    monkeypatch.setattr(reel_download, "download_cover", fake_cover)

    plan = {
        "eval_mode": "cover",
        "covers_target": 2,
        "videos_target": 0,
        "cover_reels": [
            {"reel_id": "a", "url": "https://www.instagram.com/reel/a/", "thumbnail_url": "https://x/a.jpg"},
            {"reel_id": "b", "url": "https://www.instagram.com/reel/b/", "thumbnail_url": "https://x/b.jpg"},
        ],
        "video_reels": [{"reel_id": "a", "url": "https://www.instagram.com/reel/a/"}],
    }
    result = reel_download.download_content_eval(plan, dest_dir=str(tmp_path))
    assert result["covers_downloaded"] == 2
    assert result["videos_downloaded"] == 0
    assert result["videos"] == []


def test_download_content_eval_surfaces_detail_on_failure(monkeypatch, tmp_path):
    """Cover download failures must return code+detail — not AttributeError on .message."""
    import eval_mode
    import reel_download
    from errors import DownloadError

    monkeypatch.setattr(eval_mode, "resolve_eval_mode", lambda brief=None: "cover")

    def boom(reel_url, **kw):
        raise DownloadError("thumbnail_fetch_failed", "HTTP Error 403: Forbidden")

    monkeypatch.setattr(reel_download, "download_cover", boom)

    plan = {
        "eval_mode": "cover",
        "covers_target": 1,
        "videos_target": 0,
        "cover_reels": [
            {"reel_id": "a", "url": "https://www.instagram.com/reel/a/", "thumbnail_url": "https://x/a.jpg"},
        ],
        "video_reels": [],
    }
    result = reel_download.download_content_eval(plan, dest_dir=str(tmp_path))
    assert result["covers_downloaded"] == 0
    assert result["partial"] is True
    assert result["errors"][0]["code"] == "thumbnail_fetch_failed"
    assert "403" in result["errors"][0]["message"]
    assert result["covers"][0]["ok"] is False
    assert result["covers"][0]["error_code"] == "thumbnail_fetch_failed"


def test_reel_id_from_url():
    import reel_download
    assert reel_download._reel_id_from_url("https://www.instagram.com/mrkate/reel/DXXXA2BgY-9/") == "DXXXA2BgY-9"
    assert reel_download._reel_id_from_url("https://www.instagram.com/reel/abc123_/") == "abc123_"
    assert reel_download._reel_id_from_url("https://www.instagram.com/p/CfKYsOeA3-u/") == "CfKYsOeA3-u"
    assert reel_download._reel_id_from_url("not a url") == "unknown"


def test_download_reel_ig_404_surfaces_distinct_error_code(monkeypatch):
    """yt-dlp IG 404 / empty-media → yt_dlp_ig_extractor_failed (operator knows it's upstream)."""
    import reel_download
    from errors import DownloadError

    class _P:
        returncode = 1
        stdout = ""
        stderr = "HTTP Error 404: Not Found. Instagram sent an empty media response."

    monkeypatch.setattr(reel_download, "_export_cookies", lambda: "/tmp/fake.txt")
    monkeypatch.setattr(reel_download.subprocess, "run", lambda *a, **k: _P())

    with pytest.raises(DownloadError) as exc:
        reel_download.download_reel("https://www.instagram.com/reel/abc/", dest_dir="/tmp")
    assert exc.value.code == "yt_dlp_ig_extractor_failed"


# --------------------------------------------------------------- cleanup

def test_cleanup_reels_deletes_old_files(tmp_path):
    import reel_download

    old = tmp_path / "old.mp4"
    old.write_bytes(b"x" * 100)
    # Set mtime to 2 hours ago
    import time as _time
    old_age = _time.time() - 2 * 3600
    os.utime(old, (old_age, old_age))

    new = tmp_path / "new.mp4"
    new.write_bytes(b"y" * 100)

    r = reel_download.cleanup_reels(dest_dir=str(tmp_path), older_than_hours=1.0)
    assert r["deleted_count"] == 1
    assert not old.exists()
    assert new.exists()  # recent file survives


def test_cleanup_reels_missing_dir_returns_zero():
    import reel_download
    r = reel_download.cleanup_reels(dest_dir="/nonexistent/path/xyz", older_than_hours=1.0)
    assert r["deleted_count"] == 0
    assert r["freed_mb"] == 0.0
