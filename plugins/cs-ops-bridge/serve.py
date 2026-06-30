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
        "processing_stale",
        "escalation_resume",
        "escalation_vault_cleanup",
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
        ps = pkg.processing_stale
        gw_base = os.environ.get("CS_OPS_GATEWAY_BASE", "http://127.0.0.1:8643").rstrip("/")

        async def _probe_gateway() -> None:
            import urllib.error
            import urllib.request

            await asyncio.sleep(2.0)
            url = f"{gw_base}/v1/models"
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    ok = 200 <= resp.status < 300
            except urllib.error.HTTPError as exc:
                ok = exc.code < 500
            except Exception:
                log = logging.getLogger("cs-ops-bridge")
                log.error(
                    "Gateway API unreachable at %s — CS launches will fail. "
                    "Run: hermes -p povison-cs gateway run --replace (with API_SERVER_ENABLED=true)",
                    gw_base,
                )
                return

        tasks = []
        tasks.append(asyncio.create_task(_probe_gateway(), name="cs-gateway-probe"))
        if os.environ.get("CS_OPS_QUICKCEP_WATCHER_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(qw.start_background(), name="cs-quickcep-watcher"))
        if os.environ.get("CS_OPS_FEISHU_POLLER_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(fp.start_background(), name="cs-feishu-poller"))
        if os.environ.get("CS_OPS_ESCALATION_TIMEOUT_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(et.start_background(), name="cs-escalation-timeout"))
        if os.environ.get("CS_OPS_PROCESSING_STALE_AUTO_START", "true").lower() in ("1", "true", "yes"):
            tasks.append(asyncio.create_task(ps.start_background(), name="cs-processing-stale"))
        if os.environ.get("CS_OPS_ESC_VAULT_CLEANUP_AUTO_START", "true").lower() in ("1", "true", "yes"):
            vc = pkg.escalation_vault_cleanup
            tasks.append(asyncio.create_task(vc.start_background(), name="cs-vault-cleanup"))
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
    from profile_env import load_profile_dotenv
    from profile_refs import assert_expected_profile, cs_profile_dir, cs_profile_name, quickcep_skill_dir

    load_profile_dotenv()

    assert_expected_profile(context="serve")
    log = logging.getLogger("cs-ops-bridge")
    log.info(
        "profile=%s dir=%s quickcep=%s",
        cs_profile_name(),
        cs_profile_dir(),
        quickcep_skill_dir(),
    )
    from bridge_lan import default_vault_public_base

    explicit = os.environ.get("CS_OPS_ESC_VAULT_PUBLIC_BASE", "").strip()
    vault_base = explicit.rstrip("/") if explicit else default_vault_public_base()
    log.info("vault upload base=%s (LAN experts use this host, not 127.0.0.1)", vault_base)
    import uvicorn

    uvicorn.run(_build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
