"""FastAPI factory + lifespan."""

from __future__ import annotations

import datetime as _dt
import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import _connect, init_db
from .deps import shutdown_bridge, shutdown_gateway
from .gateway_approval_watcher import watcher as approval_watcher
from .gmail_store import migrate_legacy_global_token
from .routers import (
    admin,
    approvals,
    auth,
    google_auth,
    internal,
    campaign_transfer,
    campaigns,
    candidates,
    escalations,
    events as events_router,
    facts,
    gateway_approvals,
    goals,
    kols,
    learning,
    link_preview,
    policies,
    products,
    reply_watcher,
    relationships,
)
from .security import hash_password

log = logging.getLogger("kol_ops_console")


def _ensure_owner() -> None:
    s = get_settings()
    conn = _connect(s.db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row["c"] > 0:
            return
        pwd = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO users (email, password_hash, role, is_active, created_at) VALUES (?,?,?,1,?)",
            (
                "owner@console.app",
                hash_password(pwd),
                "owner",
                _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        log.warning("=" * 60)
        log.warning("[FIRST BOOT] created user owner@console.app")
        log.warning("[FIRST BOOT] one-time password: %s", pwd)
        log.warning("[FIRST BOOT] rotate via POST /auth/users immediately.")
        log.warning("=" * 60)
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    _ensure_owner()
    conn = _connect(get_settings().db_path)
    try:
        owner = conn.execute(
            "SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1"
        ).fetchone()
        if owner:
            migrate_legacy_global_token(conn, int(owner["id"]))
    finally:
        conn.close()
    await approval_watcher.start(get_settings())
    yield
    await approval_watcher.stop()
    await events_router.hub.stop()
    await shutdown_bridge()
    await shutdown_gateway()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="KOL Ops Console", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        # Dev console may be opened through loopback or a private LAN IP
        # while the backend stays on 8765. Accept those local dev origins
        # so phone/laptop testing does not trip CORS preflight failures.
        allow_origins=s.cors_origins,
        allow_origin_regex=(
            r"^https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _slow_api_probe(request: Request, call_next):
        if not s.slow_api_log_enabled:
            return await call_next(request)
        path = request.url.path
        if s.slow_api_log_paths and not any(path.startswith(p) for p in s.slow_api_log_paths):
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - started
            if elapsed >= s.slow_api_log_threshold_sec:
                log.info(
                    "slow_api method=%s path=%s status=EXCEPTION duration_ms=%.1f",
                    request.method,
                    path,
                    elapsed * 1000.0,
                )
            raise
        elapsed = time.perf_counter() - started
        if elapsed >= s.slow_api_log_threshold_sec:
            log.info(
                "slow_api method=%s path=%s status=%s duration_ms=%.1f",
                request.method,
                path,
                response.status_code,
                elapsed * 1000.0,
            )
        return response

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "env": s.env}

    app.include_router(auth.router)
    app.include_router(google_auth.router)
    app.include_router(internal.router)
    app.include_router(products.router)
    app.include_router(kols.router)
    app.include_router(campaigns.router)
    app.include_router(candidates.router)
    app.include_router(facts.router)
    app.include_router(goals.router)
    app.include_router(relationships.router)
    app.include_router(campaign_transfer.router)
    app.include_router(escalations.router)
    app.include_router(approvals.router)
    app.include_router(learning.router)
    app.include_router(link_preview.router)
    app.include_router(gateway_approvals.router)
    app.include_router(policies.router)
    app.include_router(reply_watcher.router)
    app.include_router(admin.router)
    app.include_router(events_router.router)  # /ws
    return app


app = create_app()
