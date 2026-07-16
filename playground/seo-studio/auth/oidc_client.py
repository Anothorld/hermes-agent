"""OIDC protocol wrapper for Feishu AnyCross SSO (SEO Studio)."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

DEFAULT_SCOPE = "openid profile email offline_access"

_REQUIRED_ENV = (
    "SEO_STUDIO_OIDC_CLIENT_ID",
    "SEO_STUDIO_OIDC_CLIENT_SECRET",
    "SEO_STUDIO_OIDC_WELL_KNOWN",
    "SEO_STUDIO_OIDC_REDIRECT_URI",
)


class OIDCError(Exception):
    def __init__(self, status: int, error: str, description: str = ""):
        super().__init__(f"{error} ({status}): {description}")
        self.status = status
        self.error = error
        self.description = description


def is_configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in _REQUIRED_ENV)


def _client_id() -> str:
    return os.environ["SEO_STUDIO_OIDC_CLIENT_ID"].strip()


def _client_secret() -> str:
    return os.environ["SEO_STUDIO_OIDC_CLIENT_SECRET"].strip()


def _redirect_uri() -> str:
    return os.environ["SEO_STUDIO_OIDC_REDIRECT_URI"].strip()


def _well_known() -> str:
    return os.environ["SEO_STUDIO_OIDC_WELL_KNOWN"].strip()


def _scope() -> str:
    return os.environ.get("SEO_STUDIO_OIDC_SCOPE", DEFAULT_SCOPE).strip() or DEFAULT_SCOPE


_discovery_cache: dict[str, Any] = {}


def discovery() -> dict[str, Any]:
    if _discovery_cache:
        return _discovery_cache
    url = _well_known()
    try:
        resp = requests.get(url, timeout=10, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise OIDCError(0, "discovery_unreachable", str(exc)) from exc
    if resp.status_code != 200:
        raise OIDCError(resp.status_code, "discovery_failed", resp.text[:200])
    try:
        doc = resp.json()
    except ValueError as exc:
        raise OIDCError(0, "discovery_invalid_json", str(exc)) from exc
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not doc.get(key):
            raise OIDCError(0, "discovery_missing_endpoint", key)
    _discovery_cache.update(doc)
    return _discovery_cache


def reset_discovery_cache() -> None:
    _discovery_cache.clear()


def build_auth_url(state: str) -> str:
    doc = discovery()
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": _scope(),
        "state": state,
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    doc = discovery()
    raw = f"{_client_id()}:{_client_secret()}".encode("utf-8")
    basic = "Basic " + base64.b64encode(raw).decode("ascii")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }
    try:
        resp = requests.post(
            doc["token_endpoint"],
            data=data,
            headers={"Authorization": basic, "Accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise OIDCError(0, "token_exchange_unreachable", str(exc)) from exc
    if resp.status_code != 200:
        try:
            err = resp.json()
        except ValueError:
            err = {"error": "token_exchange_failed", "error_description": resp.text[:200]}
        raise OIDCError(
            resp.status_code,
            err.get("error", "token_exchange_failed"),
            err.get("error_description", ""),
        )
    return resp.json()


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    doc = discovery()
    try:
        resp = requests.get(
            doc["userinfo_endpoint"],
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise OIDCError(0, "userinfo_unreachable", str(exc)) from exc
    if resp.status_code != 200:
        raise OIDCError(resp.status_code, "userinfo_failed", resp.text[:200])
    info = resp.json()
    return {
        "sub": str(info.get("sub") or ""),
        "name": str(info.get("name") or ""),
        "email": str(info.get("email") or ""),
    }
