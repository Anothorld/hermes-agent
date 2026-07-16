"""Feishu H5 web-app passwordless login for SEO Studio.

Inside the Feishu client webview the frontend calls ``tt.requestAuthCode``,
POSTs the code to ``/auth/feishu/h5-token``, and this client exchanges it
via Feishu OpenAPI for user identity.

Env: SEO_STUDIO_FEISHU_APP_ID, SEO_STUDIO_FEISHU_APP_SECRET
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

_BASE = "https://open.feishu.cn"
_APP_TOKEN_CACHE: dict[str, Any] = {"token": "", "exp": 0.0}


class FeishuH5Error(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"{msg} (code={code})")
        self.code = code
        self.msg = msg


def is_configured() -> bool:
    return bool(
        os.environ.get("SEO_STUDIO_FEISHU_APP_ID", "").strip()
        and os.environ.get("SEO_STUDIO_FEISHU_APP_SECRET", "").strip()
    )


def app_id() -> str:
    return os.environ["SEO_STUDIO_FEISHU_APP_ID"].strip()


def _app_secret() -> str:
    return os.environ["SEO_STUDIO_FEISHU_APP_SECRET"].strip()


def _get_app_access_token() -> str:
    now = time.time()
    if _APP_TOKEN_CACHE["token"] and _APP_TOKEN_CACHE["exp"] > now + 60:
        return _APP_TOKEN_CACHE["token"]
    try:
        r = requests.post(
            f"{_BASE}/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": app_id(), "app_secret": _app_secret()},
            timeout=10,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FeishuH5Error(0, f"app_access_token unreachable: {exc}") from exc
    if data.get("code") != 0:
        raise FeishuH5Error(int(data.get("code", -1)), str(data.get("msg", "app_access_token failed")))
    _APP_TOKEN_CACHE["token"] = data["app_access_token"]
    _APP_TOKEN_CACHE["exp"] = now + int(data.get("expire", 7200))
    return _APP_TOKEN_CACHE["token"]


def reset_app_token_cache() -> None:
    _APP_TOKEN_CACHE["token"] = ""
    _APP_TOKEN_CACHE["exp"] = 0.0


def exchange_code(code: str) -> dict[str, Any]:
    app_token = _get_app_access_token()
    try:
        r = requests.post(
            f"{_BASE}/open-apis/authen/v1/access_token",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"grant_type": "authorization_code", "code": code},
            timeout=10,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FeishuH5Error(0, f"exchange unreachable: {exc}") from exc
    if data.get("code") != 0:
        raise FeishuH5Error(int(data.get("code", -1)), str(data.get("msg", "exchange failed")))
    if isinstance(data.get("data"), dict):
        merged = dict(data["data"])
        merged.setdefault("code", data.get("code"))
        return merged
    return data


def fetch_userinfo(user_access_token: str) -> dict[str, Any]:
    try:
        r = requests.get(
            f"{_BASE}/open-apis/authen/v1/user_info",
            headers={"Authorization": f"Bearer {user_access_token}"},
            timeout=10,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise FeishuH5Error(0, f"userinfo unreachable: {exc}") from exc
    if data.get("code") != 0:
        raise FeishuH5Error(int(data.get("code", -1)), str(data.get("msg", "userinfo failed")))
    return data.get("data", {}) or {}
