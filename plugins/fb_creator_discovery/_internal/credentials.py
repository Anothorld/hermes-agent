"""Credential resolution for the Facebook Creator Discovery plugin.

Design notes (SOLID):

* :class:`FBCredentialProvider` is a tiny ``Protocol`` (ISP/DIP) so the HTTP
  client depends on an abstraction, not on the file-system implementation.
* :class:`EnvFileCredentialProvider` is the default concrete implementation:
  environment variables override the on-disk config file, and missing or
  malformed config raises :class:`FBCreatorAuthRequiredError` with an
  actionable hint.
* The Page Access Token is **never** logged, never echoed back to the agent,
  and never written by this module — the user manages it manually.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .errors import FBCreatorAuthRequiredError


DEFAULT_API_VERSION = "v21.0"
DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "fb_creator.json"
ENV_TOKEN = "FB_CREATOR_PAGE_TOKEN"
ENV_API_VERSION = "FB_CREATOR_API_VERSION"
ENV_CONFIG_PATH = "FB_CREATOR_CONFIG_PATH"

_CONFIG_HINT = (
    "Set the FB_CREATOR_PAGE_TOKEN environment variable, or create "
    f"{DEFAULT_CONFIG_PATH} (chmod 600) with JSON: "
    '{"page_access_token": "<token>", "api_version": "v21.0"}. '
    "Obtain the Page Access Token from the Graph API Explorer with the "
    "facebook_creator_marketplace_discovery and pages_show_list permissions."
)


@dataclass(frozen=True)
class FBCredentials:
    """Immutable credential bundle consumed by the HTTP client."""

    page_access_token: str
    api_version: str = DEFAULT_API_VERSION


class FBCredentialProvider(Protocol):
    """Resolve a fresh :class:`FBCredentials` bundle on demand."""

    def resolve(self) -> FBCredentials:  # pragma: no cover - protocol
        ...

    def is_configured(self) -> bool:  # pragma: no cover - protocol
        ...


class EnvFileCredentialProvider:
    """Resolve credentials from env vars, falling back to a JSON config file.

    The lookup order is:

    1. ``FB_CREATOR_PAGE_TOKEN`` env var (token) and ``FB_CREATOR_API_VERSION``
       env var (api version, optional).
    2. JSON file at ``FB_CREATOR_CONFIG_PATH`` env var, or
       ``~/.hermes/fb_creator.json``.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path

    # -- public API -------------------------------------------------------

    def resolve(self) -> FBCredentials:
        token = os.environ.get(ENV_TOKEN, "").strip()
        api_version = os.environ.get(ENV_API_VERSION, "").strip()

        if not token:
            file_data = self._read_config_file()
            token = str(file_data.get("page_access_token") or "").strip()
            if not api_version:
                api_version = str(file_data.get("api_version") or "").strip()

        if not token:
            raise FBCreatorAuthRequiredError(
                "No Facebook Creator Discovery Page Access Token configured. "
                + _CONFIG_HINT
            )

        return FBCredentials(
            page_access_token=token,
            api_version=api_version or DEFAULT_API_VERSION,
        )

    def is_configured(self) -> bool:
        try:
            self.resolve()
        except FBCreatorAuthRequiredError:
            return False
        return True

    # -- internals --------------------------------------------------------

    def _config_file_path(self) -> Path:
        if self._config_path is not None:
            return self._config_path
        env_override = os.environ.get(ENV_CONFIG_PATH, "").strip()
        if env_override:
            return Path(env_override).expanduser()
        return DEFAULT_CONFIG_PATH

    def _read_config_file(self) -> dict:
        path = self._config_file_path()
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise FBCreatorAuthRequiredError(
                f"Failed to read FB Creator config at {path}: {exc}. "
                + _CONFIG_HINT
            ) from exc
        if not isinstance(data, dict):
            raise FBCreatorAuthRequiredError(
                f"FB Creator config at {path} must be a JSON object. "
                + _CONFIG_HINT
            )
        return data
