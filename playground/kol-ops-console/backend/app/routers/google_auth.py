"""Per-operator Gmail OAuth (Console users ↔ Google accounts)."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..audit import write_audit
from ..config import get_settings
from ..deps import current_user, get_conn, require_role
from ..gmail_store import (
    GMAIL_SCOPES,
    get_connection,
    resolve_client_secret_path,
    revoke_connection,
    upsert_connection,
)
router = APIRouter(prefix="/auth/google", tags=["google-auth"])


class GoogleStatusResponse(BaseModel):
    connected: bool
    google_email: str | None = None
    connected_at: str | None = None
    scopes: list[str] = []


class GoogleStartResponse(BaseModel):
    auth_url: str


def _require_google_deps() -> None:
    try:
        import google_auth_oauthlib  # noqa: F401
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "google-auth-oauthlib not installed on console host",
        ) from exc


def _client_secret_missing() -> bool:
    return not resolve_client_secret_path().exists()


@router.get("/status", response_model=GoogleStatusResponse)
def google_status(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(current_user)],
) -> GoogleStatusResponse:
    row = get_connection(conn, user["id"], active_only=True)
    if not row:
        return GoogleStatusResponse(connected=False)
    return GoogleStatusResponse(
        connected=True,
        google_email=row["google_email"],
        connected_at=row["connected_at"],
        scopes=row.get("scopes") or [],
    )


@router.get("/start", response_model=GoogleStartResponse)
def google_start(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> GoogleStartResponse:
    if _client_secret_missing():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "code": "google_client_secret_missing",
                "message": (
                    "Google OAuth client secret not configured. "
                    "Set KOC_GOOGLE_CLIENT_SECRET_PATH or place google_client_secret.json in HERMES_HOME."
                ),
            },
        )
    _require_google_deps()
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    flow = Flow.from_client_secrets_file(
        str(resolve_client_secret_path()),
        scopes=list(GMAIL_SCOPES),
        redirect_uri=s.google_oauth_redirect_uri,
        autogenerate_code_verifier=True,
    )
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO gmail_oauth_pending (user_id, state, code_verifier, redirect_uri, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state=excluded.state,
            code_verifier=excluded.code_verifier,
            redirect_uri=excluded.redirect_uri,
            created_at=excluded.created_at
        """,
        (user["id"], state, flow.code_verifier, s.google_oauth_redirect_uri, now),
    )
    return GoogleStartResponse(auth_url=auth_url)


@router.get("/callback")
def google_callback(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    if _client_secret_missing():
        return RedirectResponse("/settings?google=error&reason=client_secret")
    _require_google_deps()
    from google_auth_oauthlib.flow import Flow

    pending = conn.execute(
        "SELECT user_id, state, code_verifier, redirect_uri FROM gmail_oauth_pending WHERE state=?",
        (state,),
    ).fetchone()
    if not pending:
        return RedirectResponse("/settings?google=error&reason=state")
    user_id = int(pending["user_id"])
    if pending["state"] != state:
        return RedirectResponse("/settings?google=error&reason=state_mismatch")

    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    flow = Flow.from_client_secrets_file(
        str(resolve_client_secret_path()),
        scopes=list(GMAIL_SCOPES),
        redirect_uri=pending["redirect_uri"],
        state=pending["state"],
        code_verifier=pending["code_verifier"],
    )
    try:
        flow.fetch_token(code=code)
    except Exception:
        return RedirectResponse("/settings?google=error&reason=exchange")
    creds = flow.credentials
    token_payload: dict[str, Any] = json.loads(creds.to_json())
    if not token_payload.get("type"):
        token_payload["type"] = "authorized_user"
    granted = list(creds.granted_scopes or []) if creds.granted_scopes else list(GMAIL_SCOPES)
    token_payload["scopes"] = granted

    google_email = _fetch_profile_email(token_payload)
    if not google_email:
        return RedirectResponse("/settings?google=error&reason=profile")

    upsert_connection(
        conn,
        user_id=user_id,
        google_email=google_email,
        token_json=token_payload,
        scopes=granted,
    )
    conn.execute("DELETE FROM gmail_oauth_pending WHERE user_id=?", (user_id,))
    write_audit(
        conn,
        actor_user_id=user_id,
        action="gmail.connect",
        target=google_email,
    )
    return RedirectResponse("/settings?google=connected")


@router.delete("")
def google_disconnect(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[dict, Depends(require_role("owner", "operator"))],
) -> dict:
    if not revoke_connection(conn, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active gmail connection")
    write_audit(conn, actor_user_id=user["id"], action="gmail.disconnect")
    return {"ok": True}


def _fetch_profile_email(token_payload: dict[str, Any]) -> str | None:
    """Resolve Gmail profile address after OAuth."""
    import subprocess
    import sys
    import tempfile

    from pathlib import Path

    repo = Path(__file__).resolve().parents[5]
    script = repo / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
    if not script.exists():
        hermes_repo = Path(__file__).resolve().parents[6] / "hermes-agent" / script.relative_to("hermes-agent")
        if hermes_repo.exists():
            script = hermes_repo
    if not script.exists():
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        tmp.write(json.dumps(token_payload))
        tmp_path = tmp.name
    try:
        env = os.environ.copy()
        env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = tmp_path
        result = subprocess.run(
            [sys.executable, str(script), "gmail", "profile"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
        email = str(data.get("emailAddress") or "").strip().lower()
        return email or None
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
