"""Direct Nox creator GET when ``noxinfluencer`` CLI cannot pass dash-prefixed IDs."""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from internal.cli_runner import NoxCliError, NoxInsufficientCreditError, _INSUFFICIENT_CODES
from internal.nox_auth import ensure_nox_auth, read_stored_config

_ENVIRONMENT_SERVER_URLS: dict[str, str] = {
    "online": "https://skill.noxinfluencer.com",
    "pre": "http://10.101.199.107",
    "test": "http://10.101.199.107:8080",
    "dev": "http://localhost:23000",
}
_DEFAULT_ENVIRONMENT = "online"
_SUPPORTED_PLATFORMS = frozenset({"youtube", "tiktok", "instagram"})
_DIMENSION_SUFFIX: dict[str, tuple[str, Optional[str]]] = {
    "profile": ("/profile", "/profile/detail"),
    "audience": ("/audience", "/audience/detail"),
    "cooperation": ("/cooperation", "/cooperation/detail"),
    "content": ("/content", "/content/detail"),
    "contacts": ("/contacts", None),
}


def _server_url(config: dict[str, Any]) -> str:
    override = str(config.get("environment") or _DEFAULT_ENVIRONMENT).strip().lower()
    if override in _ENVIRONMENT_SERVER_URLS:
        return _ENVIRONMENT_SERVER_URLS[override]
    custom = config.get("server_url")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    return _ENVIRONMENT_SERVER_URLS[_DEFAULT_ENVIRONMENT]


def build_creator_api_path(
    dimension: str,
    *,
    creator_id: Optional[str] = None,
    url: Optional[str] = None,
    platform: Optional[str] = None,
    channel_id: Optional[str] = None,
    detail: bool = False,
    language: str = "en",
) -> str:
    """Mirror ``buildCreatorApiPath`` from ``@noxinfluencer/cli``."""
    if dimension not in _DIMENSION_SUFFIX:
        raise ValueError(f"unsupported creator dimension: {dimension}")
    base_suffix, detail_suffix = _DIMENSION_SUFFIX[dimension]
    path_suffix = detail_suffix if detail and detail_suffix else base_suffix

    cid = str(creator_id).strip() if creator_id else ""
    url_s = str(url).strip() if url else ""
    plat = str(platform).strip().lower() if platform else ""
    ch = str(channel_id).strip() if channel_id else ""

    has_id = bool(cid)
    has_url = bool(url_s)
    has_plat = bool(plat)
    has_ch = bool(ch)
    has_direct = has_url or has_plat or has_ch

    if has_id and has_direct:
        raise ValueError("creator_id and url/platform selectors are mutually exclusive")
    if not has_id and not has_direct:
        raise ValueError("creator_id or url/platform selector required")

    query: dict[str, str] = {}
    if has_url:
        if has_plat or has_ch:
            raise ValueError("url cannot be combined with platform/channel-id")
        query["url"] = url_s
        api_base = f"/api/v1/creators{path_suffix}"
    elif has_plat or has_ch:
        if has_plat != has_ch:
            raise ValueError("platform and channel-id must be provided together")
        if plat not in _SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported platform: {plat}")
        query["platform"] = plat
        query["channel_id"] = ch
        api_base = f"/api/v1/creators{path_suffix}"
    else:
        api_base = f"/api/v1/creators/{quote(cid, safe='')}{path_suffix}"

    if dimension == "content" and language:
        query.setdefault("language", language)
    if query:
        return f"{api_base}?{urlencode(query)}"
    return api_base


def _validate_envelope(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise NoxCliError("nox API returned non-object JSON")
    code = envelope.get("error_code")
    if envelope.get("success") is False and code in _INSUFFICIENT_CODES:
        raise NoxInsufficientCreditError(
            f"nox insufficient credit: {code}",
            envelope=envelope,
        )
    if envelope.get("success") is False:
        raise NoxCliError(
            f"nox API error: {code or envelope.get('summary') or 'unknown'}",
            envelope=envelope,
        )
    return envelope


def fetch_creator_read(
    dimension: str,
    *,
    creator_id: Optional[str] = None,
    url: Optional[str] = None,
    platform: Optional[str] = None,
    channel_id: Optional[str] = None,
    detail: bool = False,
    lang: str = "en",
    env_mode: str = "LIVE",
    timeout: int = 120,
) -> dict[str, Any]:
    """GET creator dimension via HTTP (same envelope as ``noxinfluencer -j``)."""
    if env_mode.upper() == "TEST":
        raise NoxCliError("fetch_creator_read should not be called in TEST")

    ensure_nox_auth(env_mode)
    config = read_stored_config()
    api_key = config.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise NoxCliError("nox API key missing after auth preflight")

    api_path = build_creator_api_path(
        dimension,
        creator_id=creator_id,
        url=url,
        platform=platform,
        channel_id=channel_id,
        detail=detail,
        language=lang,
    )
    full_url = f"{_server_url(config)}{api_path}"
    req = Request(
        full_url,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "X-Request-ID": str(uuid.uuid4()),
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except OSError as exc:
        raise NoxCliError(f"nox HTTP request failed: {exc}") from exc

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NoxCliError(f"invalid JSON from nox API: {exc}") from exc
    return _validate_envelope(envelope)


__all__ = [
    "build_creator_api_path",
    "fetch_creator_read",
]
