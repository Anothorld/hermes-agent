"""Standalone runner for cs-intent-classifier.

Usage::

    python plugins/cs-intent-classifier/serve.py --host 127.0.0.1 --port 8082

Independent of cs-ops-bridge and the povison-cs profile — reads only its own
CS_INTENT_* env vars. Enable in cs-ops-bridge by setting CS_INTENT_ENABLED=true.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI

_PLUGIN_ROOT = Path(__file__).resolve().parent
_PKG_NAME = "cs_intent_classifier_pkg"


def _load_pkg() -> "object":
    """Load the plugin modules as a synthetic package (mirrors cs-ops-bridge)."""
    import importlib.util
    from types import ModuleType

    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in ("schemas", "db", "classifier", "intent_provider", "plugin_api"):
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
    app = FastAPI(title="cs-intent-classifier")
    app.include_router(pkg.plugin_api.router)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cs-intent-classifier API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CS_INTENT_PORT", "8082")),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("cs-intent-classifier")
    log.info(
        "starting host=%s port=%s model=%s llm_configured=%s",
        args.host,
        args.port,
        os.environ.get("CS_INTENT_LLM_MODEL", "(unconfigured)"),
        bool(os.environ.get("CS_INTENT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")),
    )
    import uvicorn

    uvicorn.run(_build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
