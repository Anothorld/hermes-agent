"""Standalone runner for cs-ops-bridge.

Usage::

    python plugins/cs-ops-bridge/serve.py --host 127.0.0.1 --port 8081
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
_PKG_NAME = "cs_ops_bridge_pkg"
_MOUNT = "/api/plugins/cs-ops-bridge"


def _load_pkg() -> ModuleType:
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in (
        "schema",
        "cal",
        "classify_intent",
        "bridge_agent_contract",
        "gateway_client",
        "gateway_launch",
        "quickcep_watcher",
        "feishu_escalation_poller",
        "escalation_timeout",
        "escalation_resume",
        "plugin_api",
    ):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG_NAME
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


def _build_app() -> FastAPI:
    pkg = _load_pkg()
    router = pkg.plugin_api.router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        qw = pkg.quickcep_watcher
        fp = pkg.feishu_escalation_poller
        et = pkg.escalation_timeout
        tasks = []
        if os.environ.get("CS_OPS_QUICKCEP_WATCHER_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(qw.start_background(), name="cs-quickcep-watcher"))
        if os.environ.get("CS_OPS_FEISHU_POLLER_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(fp.start_background(), name="cs-feishu-poller"))
        if os.environ.get("CS_OPS_ESCALATION_TIMEOUT_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(et.start_background(), name="cs-escalation-timeout"))
        yield
        qw.request_stop()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="cs-ops-bridge", lifespan=lifespan)
    app.include_router(router, prefix=_MOUNT)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cs-ops-bridge API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(_build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
