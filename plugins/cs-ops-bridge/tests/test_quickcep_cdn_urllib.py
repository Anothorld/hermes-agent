"""Tests for quickcep_cdn.upload_file_to_cdn — stdlib urllib multipart upload.

Covers the regression from session 2563708874701037569: the old implementation
shelled out to ``quickcep_cli.py upload-file`` which ``import requests`` and
failed under the gateway's system python3. These tests pin the new in-process
stdlib path: correct multipart field names, feature passthrough, CDN url
parsing, and graceful error handling — with no ``requests`` dependency.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_cdn_test"


def _load_module(sub: str):
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.{sub}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / f"{sub}.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    setattr(sys.modules[_PKG], sub, mod)
    return mod


@pytest.fixture()
def cdn_module():
    return _load_module("quickcep_cdn")


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def _make_profile(tmp_path: Path, *, expires_at: float = 1e12) -> Path:
    """Create a fake quickcep skill dir with a fresh .quickcep_token.json."""
    skill = tmp_path / "skills" / "social-media" / "quickcep"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / ".quickcep_token.json").write_text(
        json.dumps({"jwt": "JWT-XYZ", "expires_at": expires_at})
    )
    return skill


def test_upload_builds_multipart_with_correct_fields(cdn_module, tmp_path):
    skill = _make_profile(tmp_path)
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResp(json.dumps({"code": 200, "data": "https://cdn.example.com/x.jpg"}).encode())

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill), \
         patch.object(cdn_module.urllib.request, "urlopen", side_effect=fake_urlopen):
        result = cdn_module.upload_file_to_cdn(img, feature="email")

    assert result["ok"] is True
    assert result["url"] == "https://cdn.example.com/x.jpg"
    assert result["attachment"] == {
        "fileName": "photo.jpg",
        "fileSize": len(b"\xff\xd8\xff\xe0FAKEJPEG"),
        "url": "https://cdn.example.com/x.jpg",
    }

    body = captured["body"]
    assert b'name="file"' in body and b'filename="photo.jpg"' in body
    assert b'name="mainModule"' in body and b"message-center" in body
    assert b'name="subModule"' in body and b"im" in body
    assert b'name="feature"' in body and b"email" in body
    assert captured["headers"]["Content-type"].startswith("multipart/form-data; boundary=")
    assert captured["headers"]["Quick-token"] == "JWT-XYZ"
    assert captured["url"].endswith("/robot-configuration/api/file/uploadFile")


def test_upload_defaults_feature_to_send_image_when_empty(cdn_module, tmp_path):
    skill = _make_profile(tmp_path)
    img = tmp_path / "a.png"
    img.write_bytes(b"PNGDATA")

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data
        return _FakeResp(json.dumps({"code": 200, "data": "https://cdn/u"}).encode())

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill), \
         patch.object(cdn_module.urllib.request, "urlopen", side_effect=fake_urlopen):
        cdn_module.upload_file_to_cdn(img, feature="")

    assert b"send-image" in captured["body"]


def test_upload_returns_error_when_no_cached_token(cdn_module, tmp_path):
    # skill dir exists but no token file
    skill = tmp_path / "skills" / "social-media" / "quickcep"
    (skill / "scripts").mkdir(parents=True)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"data")

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill):
        result = cdn_module.upload_file_to_cdn(img)

    assert result["ok"] is False
    assert "token" in result["error"]


def test_upload_returns_error_when_token_expired(cdn_module, tmp_path):
    skill = _make_profile(tmp_path, expires_at=0.0)  # already expired
    img = tmp_path / "x.jpg"
    img.write_bytes(b"data")

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill):
        result = cdn_module.upload_file_to_cdn(img)

    assert result["ok"] is False
    assert "token" in result["error"]


def test_upload_returns_error_on_http_error(cdn_module, tmp_path):
    import io
    import urllib.error

    skill = _make_profile(tmp_path)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"data")

    err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"boom"))
    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill), \
         patch.object(cdn_module.urllib.request, "urlopen", side_effect=err):
        result = cdn_module.upload_file_to_cdn(img)

    assert result["ok"] is False
    assert "HTTP 500" in result["error"]
    assert "boom" in result["body"]


def test_upload_returns_error_when_cdn_url_empty(cdn_module, tmp_path):
    skill = _make_profile(tmp_path)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"data")

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill), \
         patch.object(cdn_module.urllib.request, "urlopen", return_value=_FakeResp(json.dumps({"code": 200, "data": ""}).encode())):
        result = cdn_module.upload_file_to_cdn(img)

    assert result["ok"] is False
    assert "empty url" in result["error"]


def test_upload_returns_error_when_api_code_not_200(cdn_module, tmp_path):
    skill = _make_profile(tmp_path)
    img = tmp_path / "x.jpg"
    img.write_bytes(b"data")

    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill), \
         patch.object(
             cdn_module.urllib.request,
             "urlopen",
             return_value=_FakeResp(json.dumps({"code": 500, "data": "https://cdn/x"}).encode()),
         ):
        result = cdn_module.upload_file_to_cdn(img)

    assert result["ok"] is False
    assert "code 500" in result["error"]


def test_upload_missing_file_returns_error(cdn_module, tmp_path):
    skill = _make_profile(tmp_path)
    with patch.object(cdn_module, "quickcep_skill_dir", return_value=skill):
        result = cdn_module.upload_file_to_cdn(tmp_path / "nope.jpg")
    assert result["ok"] is False
    assert "file not found" in result["error"]


def test_build_multipart_body_shape(cdn_module):
    boundary = "BND"
    body = cdn_module._build_multipart_body(
        [
            ("file", ("hi.jpg", b"BYTES")),
            ("feature", (None, b"email")),
        ],
        boundary,
    )
    assert body.startswith(b"--BND\r\n")
    assert body.endswith(b"--BND--\r\n")
    assert b'filename="hi.jpg"' in body
    assert b'Content-Type: image/jpeg' in body  # guessed from .jpg
    assert b'name="feature"' in body
    assert b"BYTES" in body and b"email" in body
