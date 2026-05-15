"""Browser Use cloud browser provider."""

import logging
import os
import threading
import uuid
from typing import Any, Dict, Mapping, Optional

import requests

from tools.browser_providers.base import CloudBrowserProvider
from tools.managed_tool_gateway import resolve_managed_tool_gateway
from tools.tool_backend_helpers import managed_nous_tools_enabled, prefers_gateway

logger = logging.getLogger(__name__)
_pending_create_keys: Dict[str, str] = {}
_pending_create_keys_lock = threading.Lock()

_BASE_URL = "https://api.browser-use.com/api/v3"
_DEFAULT_MANAGED_TIMEOUT_MINUTES = 5
_DEFAULT_MANAGED_PROXY_COUNTRY_CODE = "us"


def _get_or_create_pending_create_key(task_id: str) -> str:
    with _pending_create_keys_lock:
        existing = _pending_create_keys.get(task_id)
        if existing:
            return existing

        created = f"browser-use-session-create:{uuid.uuid4().hex}"
        _pending_create_keys[task_id] = created
        return created


def _clear_pending_create_key(task_id: str) -> None:
    with _pending_create_keys_lock:
        _pending_create_keys.pop(task_id, None)


def _should_preserve_pending_create_key(response: requests.Response) -> bool:
    if response.status_code >= 500:
        return True

    if response.status_code != 409:
        return False

    try:
        payload = response.json()
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False

    error = payload.get("error")
    if not isinstance(error, dict):
        return False

    message = str(error.get("message") or "").lower()
    return "already in progress" in message


class BrowserUseProvider(CloudBrowserProvider):
    """Browser Use (https://browser-use.com) cloud browser backend."""

    def provider_name(self) -> str:
        return "Browser Use"

    def is_configured(self) -> bool:
        return self._get_config_or_none() is not None

    # ------------------------------------------------------------------
    # Config resolution (direct API key OR managed Nous gateway)
    # ------------------------------------------------------------------

    def _get_config_or_none(self) -> Optional[Dict[str, Any]]:
        api_key = os.environ.get("BROWSER_USE_API_KEY")
        if api_key and not prefers_gateway("browser"):
            return {
                "api_key": api_key,
                "base_url": _BASE_URL,
                "managed_mode": False,
            }

        managed = resolve_managed_tool_gateway("browser-use")
        if managed is None:
            return None

        return {
            "api_key": managed.nous_user_token,
            "base_url": managed.gateway_origin.rstrip("/"),
            "managed_mode": True,
        }

    def _get_config(self) -> Dict[str, Any]:
        config = self._get_config_or_none()
        if config is None:
            message = (
                "Browser Use requires a direct BROWSER_USE_API_KEY credential."
            )
            if managed_nous_tools_enabled():
                message = (
                    "Browser Use requires either a direct BROWSER_USE_API_KEY "
                    "credential or a managed Browser Use gateway configuration."
                )
            raise ValueError(message)
        return config

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Browser-Use-API-Key": config["api_key"],
        }
        return headers

    @staticmethod
    def _resolve_profile_id(
        session_options: Optional[Mapping[str, Any]],
    ) -> Optional[str]:
        """Resolve the Browser Use profile id to attach to a new session.

        Resolution order:
          1. Explicit ``session_options["profile_id"]`` (caller override).
          2. ``BROWSER_USE_PROFILE_ID`` environment variable.

        Returns ``None`` when neither is set, in which case Browser Use will
        create an ephemeral profile for the session.
        """

        if session_options:
            candidate = session_options.get("profile_id")
            if candidate:
                return str(candidate).strip() or None

        env_value = os.environ.get("BROWSER_USE_PROFILE_ID")
        if env_value:
            stripped = env_value.strip()
            if stripped:
                return stripped
        return None

    @staticmethod
    def _resolve_proxy_country_code(
        session_options: Optional[Mapping[str, Any]],
        *,
        managed_mode: bool,
    ) -> Optional[str]:
        """Resolve the Browser Use ``proxyCountryCode`` for a new session.

        Resolution order:
          1. Explicit ``session_options["proxy_country_code"]``.
          2. ``BROWSER_USE_PROXY_COUNTRY_CODE`` env var.
          3. Default ``"us"`` (Browser Use Cloud always routes through
             residential proxies; the code only selects the egress region).

        Pass an explicit empty string via ``session_options`` to suppress.
        """
        if session_options is not None and "proxy_country_code" in session_options:
            raw = session_options.get("proxy_country_code")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return None
            return str(raw).strip().lower()

        env_value = (os.environ.get("BROWSER_USE_PROXY_COUNTRY_CODE") or "").strip()
        if env_value:
            return env_value.lower()

        # Preserve historical default for both managed and direct sessions.
        del managed_mode  # currently identical for both modes
        return _DEFAULT_MANAGED_PROXY_COUNTRY_CODE

    def create_session(
        self,
        task_id: str,
        *,
        session_options: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, object]:
        config = self._get_config()
        managed_mode = bool(config.get("managed_mode"))

        headers = self._headers(config)
        if managed_mode:
            headers["X-Idempotency-Key"] = _get_or_create_pending_create_key(task_id)

        # Keep gateway-backed sessions short so billing authorization does not
        # default to a long Browser-Use timeout when Hermes only needs a task-
        # scoped ephemeral browser.
        payload: Dict[str, Any] = (
            {"timeout": _DEFAULT_MANAGED_TIMEOUT_MINUTES}
            if managed_mode
            else {}
        )

        # Browser Use Cloud sessions route through residential proxies; the
        # ``proxyCountryCode`` field only chooses the egress region. We send
        # it in both managed and direct modes so bot-detection behaviour is
        # consistent and Hermes can honestly advertise ``proxies=True`` in
        # features below.
        proxy_country_code = self._resolve_proxy_country_code(
            session_options, managed_mode=managed_mode
        )
        if proxy_country_code:
            payload["proxyCountryCode"] = proxy_country_code

        # Attach a persistent Browser Use profile when the caller (or the
        # operator via env var) requests one.  The v3 API accepts a
        # ``profileId`` (string<uuid>) on POST /browsers — see
        # https://docs.browser-use.com/cloud/openapi/v3.json.
        profile_id = self._resolve_profile_id(session_options)
        if profile_id:
            payload["profileId"] = profile_id

        response = requests.post(
            f"{config['base_url']}/browsers",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if not response.ok:
            if managed_mode and not _should_preserve_pending_create_key(response):
                _clear_pending_create_key(task_id)
            raise RuntimeError(
                f"Failed to create Browser Use session: "
                f"{response.status_code} {response.text}"
            )

        session_data = response.json()
        if managed_mode:
            _clear_pending_create_key(task_id)
        session_name = f"hermes_{task_id}_{uuid.uuid4().hex[:8]}"
        external_call_id = response.headers.get("x-external-call-id") if managed_mode else None

        logger.info("Created Browser Use session %s", session_name)

        cdp_url = session_data.get("cdpUrl") or session_data.get("connectUrl") or ""

        features: Dict[str, Any] = {"browser_use": True}
        if proxy_country_code:
            features["proxies"] = True
            features["proxy_country_code"] = proxy_country_code
        if profile_id:
            features["persistent_profile"] = True

        return {
            "session_name": session_name,
            "bb_session_id": session_data["id"],
            "cdp_url": cdp_url,
            "features": features,
            "external_call_id": external_call_id,
        }

    def close_session(self, session_id: str) -> bool:
        try:
            config = self._get_config()
        except ValueError:
            logger.warning("Cannot close Browser Use session %s — missing credentials", session_id)
            return False

        try:
            response = requests.patch(
                f"{config['base_url']}/browsers/{session_id}",
                headers=self._headers(config),
                json={"action": "stop"},
                timeout=10,
            )
            if response.status_code in {200, 201, 204}:
                logger.debug("Successfully closed Browser Use session %s", session_id)
                return True
            else:
                logger.warning(
                    "Failed to close Browser Use session %s: HTTP %s - %s",
                    session_id,
                    response.status_code,
                    response.text[:200],
                )
                return False
        except Exception as e:
            logger.error("Exception closing Browser Use session %s: %s", session_id, e)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        config = self._get_config_or_none()
        if config is None:
            logger.warning("Cannot emergency-cleanup Browser Use session %s — missing credentials", session_id)
            return
        try:
            requests.patch(
                f"{config['base_url']}/browsers/{session_id}",
                headers=self._headers(config),
                json={"action": "stop"},
                timeout=5,
            )
        except Exception as e:
            logger.debug("Emergency cleanup failed for Browser Use session %s: %s", session_id, e)
