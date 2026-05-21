"""FastAPI factory + lifespan."""

from __future__ import annotations

import datetime as _dt
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import _connect, init_db
from .deps import shutdown_bridge, shutdown_gateway
from .routers import (
    admin,
    auth,
    campaigns,
    content,
    contract,
    drafts,
    escalations,
    events as events_router,
    facts,
    goals,
    kols,
    logistics,
    policies,
    products,
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
    yield
    await events_router.hub.stop()
    await shutdown_bridge()
    await shutdown_gateway()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="KOL Ops Console", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "env": s.env}

    app.include_router(auth.router)
    app.include_router(products.router)
    app.include_router(kols.router)
    app.include_router(drafts.router)
    app.include_router(contract.router)
    app.include_router(logistics.router)
    app.include_router(content.router)
    app.include_router(campaigns.router)
    app.include_router(facts.router)
    app.include_router(goals.router)
    app.include_router(relationships.router)
    app.include_router(escalations.router)
    app.include_router(policies.router)
    app.include_router(admin.router)
    app.include_router(events_router.router)  # /ws
    return app


app = create_app()
