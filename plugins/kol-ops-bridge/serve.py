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

import json
import time

from fastapi import FastAPI
from starlette.requests import Request


_PLUGIN_ROOT = Path(__file__).resolve().parent
_DEBUG_LOG = Path("/Users/arnold/agent_prj/.cursor/debug-a8fb01.log")
_ACTIVE_REQUESTS = 0


def _dbg_log(
    *,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "a8fb01",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion
_PKG_NAME = "kol_ops_bridge_pkg"
_MOUNT = "/api/plugins/kol-ops-bridge"


def _load_pkg() -> ModuleType:
    """Synthesise a package so relative imports inside cal/plugin_api work."""
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in (
        "schema",
        "cal",
        "gmail_client",
        "gmail_poller",
        "gmail_inbound_poller",
        "gmail_worker",
        "plugin_api",
    ):
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
    gmail_inbound_poller = sys.modules[f"{_PKG_NAME}.gmail_inbound_poller"]
    gmail_worker = sys.modules[f"{_PKG_NAME}.gmail_worker"]

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # Gmail: unified coordinator (default) or legacy parallel pollers.
        tasks: list[asyncio.Task[None]] = []
        if gmail_worker.parallel_mode_enabled():
            logging.getLogger(__name__).info(
                "[serve] gmail parallel mode (KOL_OPS_GMAIL_WORKER_PARALLEL=1)"
            )
            if os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_POLLER") != "1":
                tasks.append(
                    asyncio.create_task(
                        gmail_poller.run_forever(),
                        name="kol-ops-bridge-gmail-sent-poller",
                    )
                )
            if os.environ.get("KOL_OPS_BRIDGE_DISABLE_GMAIL_INBOUND_POLLER") != "1":
                tasks.append(
                    asyncio.create_task(
                        gmail_inbound_poller.run_forever(),
                        name="kol-ops-bridge-gmail-inbound-poller",
                    )
                )
        else:
            tasks.append(
                asyncio.create_task(
                    gmail_worker.run_forever(),
                    name="kol-ops-bridge-gmail-worker",
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    app = FastAPI(title="kol-ops-bridge (standalone)", lifespan=_lifespan)
    app.include_router(plugin_api.router, prefix=_MOUNT)

    @app.middleware("http")
    async def _perf_probe(request: Request, call_next):
        global _ACTIVE_REQUESTS
        path = request.url.path
        if not path.startswith(_MOUNT):
            return await call_next(request)
        t0 = time.perf_counter()
        _ACTIVE_REQUESTS += 1
        active = _ACTIVE_REQUESTS
        try:
            response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            if elapsed_ms >= 500 or active >= 4:
                _dbg_log(
                    location="serve.py:middleware",
                    message="slow_or_contended_request",
                    data={
                        "method": request.method,
                        "path": path,
                        "elapsed_ms": elapsed_ms,
                        "active_requests": active,
                        "status": response.status_code,
                    },
                    hypothesis_id="H2" if active >= 4 else "H3",
                )
            return response
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            _dbg_log(
                location="serve.py:middleware",
                message="request_failed",
                data={
                    "method": request.method,
                    "path": path,
                    "elapsed_ms": elapsed_ms,
                    "active_requests": active,
                    "error": type(exc).__name__,
                },
                hypothesis_id="H2",
            )
            raise
        finally:
            _ACTIVE_REQUESTS -= 1

    @app.get("/")
    def root() -> dict:
        return {"ok": True, "mount": _MOUNT}

    return app


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and not os.environ.get(key):
            os.environ[key] = val


def _hydrate_env() -> None:
    """Load Hermes + plugin .env so learning LLM can resolve credentials."""
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    _load_env_file(hermes_home / ".env")
    _load_env_file(_PLUGIN_ROOT / ".env")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kol-ops-bridge")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)

    import uvicorn

    _hydrate_env()
    from logging.handlers import RotatingFileHandler

    log_dir = Path(
        os.environ.get(
            "KOL_OPS_BRIDGE_LOG_DIR",
            str(Path.home() / ".hermes" / "kol-ops-bridge"),
        )
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    bridge_log = log_dir / "bridge.log"
    root = logging.getLogger()
    root.setLevel(args.log_level.upper())
    if not any(
        isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", "") == str(bridge_log)
        for h in root.handlers
    ):
        fh = RotatingFileHandler(
            bridge_log, maxBytes=10_000_000, backupCount=5, encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(fh)
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
