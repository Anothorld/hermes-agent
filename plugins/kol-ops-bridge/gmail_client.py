"""Thin wrapper around the bundled `google-workspace` skill's CLI.

This plugin does NOT re-implement OAuth or the Gmail API surface. Instead
it subprocess-calls the already-shipped helper at
``skills/productivity/google-workspace/scripts/google_api.py``, which:

* reuses ``~/.hermes/google_token.json`` (managed by ``setup.py``);
* outputs JSON on stdout for every supported command;
* prefers the ``gws`` binary when available, falling back to the Python
  Google client libraries — both behaviours are transparent to us.

Design notes (LoD-compliant):
- The only thing this module knows about Gmail is *which CLI subcommand
  to call*. It never imports Google libs directly.
- All side effects are pure subprocess invocations + JSON parsing.
- On any error (missing token, network, malformed output) we return
  ``None`` / raise :class:`GmailUnavailable` so the bridge can degrade
  gracefully — Gmail integration is best-effort, never blocking.

Typical use::

    client = GmailClient()
    if client.is_available():
        result = client.create_draft(
            to="kol@example.com", subject="Hi", body="..."
        )
        # result -> {"draftId": ..., "messageId": ..., "threadId": ...}
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# Resolve once at import time — both paths are static under HERMES_HOME.
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_TOKEN_PATH = _HERMES_HOME / "google_token.json"

# Locate the bundled skill relative to this file:
# plugins/kol-ops-bridge/gmail_client.py
#   -> ../../skills/productivity/google-workspace/scripts/google_api.py
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOOGLE_API_PY = (
    _REPO_ROOT
    / "skills"
    / "productivity"
    / "google-workspace"
    / "scripts"
    / "google_api.py"
)


class GmailUnavailable(RuntimeError):
    """Raised when Gmail cannot be reached (missing token, missing CLI, etc)."""


@dataclass(frozen=True)
class DraftResult:
    """Successful Gmail draft creation."""

    draft_id: str
    message_id: str
    thread_id: str


@dataclass(frozen=True)
class GmailMessage:
    """Minimal envelope used by the reply poller."""

    message_id: str
    thread_id: str
    from_addr: str
    to: str
    subject: str
    snippet: str
    in_reply_to: Optional[str]
    references: Optional[str]
    date: str
    body: str


class GmailClient:
    """Adapter around `google_api.py`. All methods are blocking I/O."""

    def __init__(
        self,
        *,
        python_executable: Optional[str] = None,
        google_api_path: Optional[Path] = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self._python = python_executable or sys.executable
        self._script = google_api_path or _GOOGLE_API_PY
        self._timeout = timeout_sec

    # -- availability --------------------------------------------------------

    def is_available(self) -> bool:
        """True iff token + script are present (we can attempt a call)."""
        return _TOKEN_PATH.exists() and self._script.exists()

    @property
    def token_path(self) -> Path:
        return _TOKEN_PATH

    # -- write ---------------------------------------------------------------

    def create_draft(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        html: bool = False,
    ) -> DraftResult:
        """Create a Gmail draft. Returns IDs; raises on any failure."""
        if not to:
            raise GmailUnavailable("recipient (to) is required")
        cmd = [
            "gmail", "draft",
            "--to", to,
            "--subject", subject,
            "--body", body,
        ]
        if cc:
            cmd.extend(["--cc", cc])
        if html:
            cmd.append("--html")
        payload = self._invoke(cmd)
        return DraftResult(
            draft_id=str(payload.get("draftId", "")),
            message_id=str(payload.get("messageId", "")),
            thread_id=str(payload.get("threadId", "")),
        )

    # -- read ----------------------------------------------------------------

    def search(
        self,
        *,
        query: str,
        max_results: int = 25,
    ) -> list[GmailMessage]:
        """Run a Gmail search. Returns parsed envelopes (best-effort)."""
        payload = self._invoke(
            ["gmail", "search", query, "--max", str(max_results)]
        )
        # `gmail search` returns a list[ {id, threadId, from, to, subject, date,
        # snippet, labels} ] — but no body/headers. To get In-Reply-To we need
        # `gmail get`. We fetch full envelopes lazily for matched candidates;
        # here we surface the cheap list and let callers decide.
        if not isinstance(payload, list):
            return []
        out: list[GmailMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            out.append(
                GmailMessage(
                    message_id=str(item.get("id", "")),
                    thread_id=str(item.get("threadId", "")),
                    from_addr=str(item.get("from", "")),
                    to=str(item.get("to", "")),
                    subject=str(item.get("subject", "")),
                    snippet=str(item.get("snippet", "")),
                    in_reply_to=None,  # not present in list output
                    references=None,
                    date=str(item.get("date", "")),
                    body="",
                )
            )
        return out

    def get_message(self, message_id: str) -> GmailMessage:
        """Fetch a single message with headers + body."""
        payload = self._invoke(["gmail", "get", message_id])
        if not isinstance(payload, dict):
            raise GmailUnavailable(f"gmail get returned non-dict for {message_id}")
        headers = payload.get("headers") or {}
        # google_api.py's `gmail get` flattens headers into a dict.
        return GmailMessage(
            message_id=str(payload.get("id", message_id)),
            thread_id=str(payload.get("threadId", "")),
            from_addr=str(headers.get("From", "")),
            to=str(headers.get("To", "")),
            subject=str(headers.get("Subject", "")),
            snippet=str(payload.get("snippet", "")),
            in_reply_to=(headers.get("In-Reply-To") or None),
            references=(headers.get("References") or None),
            date=str(headers.get("Date", "")),
            body=str(payload.get("body", "")),
        )

    # -- internals -----------------------------------------------------------

    def _invoke(self, args: list[str]) -> Any:
        if not self.is_available():
            raise GmailUnavailable(
                f"google_token.json missing at {_TOKEN_PATH} — run "
                f"`python {self._script.parent / 'setup.py'}` first."
            )
        cmd = [self._python, str(self._script), *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GmailUnavailable(f"gmail call timed out: {exc}") from exc
        except FileNotFoundError as exc:
            raise GmailUnavailable(f"python executable missing: {exc}") from exc

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise GmailUnavailable(
                f"gmail call failed (exit {result.returncode}): {err[:500]}"
            )

        stdout = (result.stdout or "").strip()
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise GmailUnavailable(
                f"gmail call returned non-JSON: {stdout[:200]}"
            ) from exc


# Module-level singleton for convenience — callers may also construct
# their own client if they need a non-default python or script path.
_default_client: Optional[GmailClient] = None


def default_client() -> GmailClient:
    global _default_client
    if _default_client is None:
        _default_client = GmailClient()
    return _default_client
