"""Standalone runner for the kol-ops-bridge plugin (hyphenated dir-safe).

In production the plugin's APIRouter is mounted by the Hermes dashboard
host under ``/api/plugins/kol-ops-bridge/``. For local development
(and for driving the external KOL Ops Console without booting the
full dashboard), this script wraps the same router in a minimal
FastAPI app under that exact prefix.

The plugin directory is hyphenated, so we can't do
``from kol_ops_bridge import plugin_api``. Instead we load the sibling
``plugin_api.py`` as a synthetic package member, mirroring the trick
used in ``tests/conftest.py``.

Usage::

    python plugins/kol-ops-bridge/serve.py --host 127.0.0.1 --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI


_PLUGIN_ROOT = Path(__file__).resolve().parent
_PKG_NAME = "kol_ops_bridge_pkg"
_MOUNT = "/api/plugins/kol-ops-bridge"


def _load_pkg() -> ModuleType:
    """Synthesise a package so relative imports inside cal/plugin_api work."""
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in ("schema", "cal", "gmail_client", "gmail_poller", "plugin_api"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}", _PLUGIN_ROOT / f"{sub}.py"
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        spec.loader.exec_module(mod)
    return pkg


def create_app() -> FastAPI:
    _load_pkg()
    plugin_api = sys.modules[f"{_PKG_NAME}.plugin_api"]
    gmail_poller = sys.modules[f"{_PKG_NAME}.gmail_poller"]

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Gmail reply-poller runs as a single bg task; opt-out via env var.
        if os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_POLLER") == "1":
            logging.getLogger(__name__).info(
                "[serve] gmail poller disabled via env var"
            )
            yield
            return
        task = asyncio.create_task(
            gmail_poller.run_forever(),
            name="kol-ops-bridge-gmail-poller",
        )
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    app = FastAPI(title="kol-ops-bridge (standalone)", lifespan=_lifespan)
    app.include_router(plugin_api.router, prefix=_MOUNT)

    @app.get("/")
    def root() -> dict:
        return {"ok": True, "mount": _MOUNT}

    return app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kol-ops-bridge")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)

    import uvicorn

    logging.basicConfig(level=args.log_level.upper())
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
