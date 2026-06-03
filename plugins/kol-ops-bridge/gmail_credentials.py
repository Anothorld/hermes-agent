"""Resolve per-operator Gmail token paths (written by KOL Ops Console)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .gmail_client import GmailClient
from .gmail_console import refresh_token_file_from_console

_DEFAULT_DIR = Path(
    os.environ.get(
        "KOC_GMAIL_TOKENS_DIR",
        str(Path.home() / ".hermes" / "kol-ops" / "gmail_tokens"),
    ),
).expanduser()


def tokens_dir() -> Path:
    return _DEFAULT_DIR


def token_path_for_user(user_id: int) -> Path:
    return tokens_dir() / f"{int(user_id)}.json"


def legacy_token_path() -> Path:
    hermes = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    legacy_file = tokens_dir() / "legacy.json"
    if legacy_file.exists():
        return legacy_file
    return hermes / "google_token.json"


def ensure_token_path(user_id: int) -> Path | None:
    """Return on-disk token path, refreshing from Console when missing."""
    path = token_path_for_user(user_id)
    if path.exists():
        return path
    refreshed = refresh_token_file_from_console(user_id)
    return refreshed


def client_for_user(user_id: Optional[int]) -> GmailClient:
    if user_id is not None and user_id > 0:
        path = ensure_token_path(user_id) or token_path_for_user(user_id)
        if path.exists():
            return GmailClient(credentials_path=path)
    return GmailClient(credentials_path=legacy_token_path())
