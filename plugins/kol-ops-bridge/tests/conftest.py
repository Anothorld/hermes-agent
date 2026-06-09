"""Pytest fixtures for kol-ops-bridge CAL tests.

Loads ``cal.py`` and ``schema.py`` as members of a synthetic package
``kol_ops_bridge_pkg`` so the existing relative imports keep working
despite the hyphenated plugin directory not being a valid Python
identifier.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_pkg"


def pytest_configure(config):  # noqa: ARG001
    _load_package()


def _load_package() -> types.ModuleType:
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG_NAME] = pkg

    for sub in ("schema", "campaign_nox_integration", "goals", "policies", "outreach_touch",
                "prior_touch_allowlist", "cal", "discovery_skip",
                "discovery_router",
                "confirmed_ingest", "confirmed_fact_buffer",
                "pricing_engine", "campaign_validation", "classifier_facts",
                "dispatch_router", "reply_draft", "reply_chase", "orphan_gmail_draft", "reject_tags", "reply_diff",
                "learning_store", "learning_llm", "learning_distill", "learning_jobs",
                "learning_job_store", "learning_promote", "learning_overview",
                "learning_outcome",
                "gmail_reconcile",
                "gmail_client",
                "gmail_thread_resolve",
                "gmail_credentials",
                "gmail_console",
                "mailbox_resolver",
                "mailbox_escalation",
                "email_conversation",
                "classifier_eval_runner"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    _load_inbound_reply_modules(pkg)
    return pkg


def _load_inbound_reply_modules(pkg: types.ModuleType) -> None:
    """Load inbound_reply package for poller tests."""
    inbound_root = _PLUGIN_ROOT / "inbound_reply"
    ordered = [
        "schemas",
        "gateway_client",
        "deps",
        "event_helpers",
        "gating",
        "matcher",
        "payload",
        "processor",
        "recovery",
        "state",
        "orchestrator",
    ]
    for name in ordered:
        key = f"{_PKG_NAME}.inbound_reply.{name}"
        if key in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(key, inbound_root / f"{name}.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)

    init_key = f"{_PKG_NAME}.inbound_reply"
    if init_key not in sys.modules:
        spec = importlib.util.spec_from_file_location(init_key, inbound_root / "__init__.py")
        assert spec is not None and spec.loader is not None
        init_mod = importlib.util.module_from_spec(spec)
        sys.modules[init_key] = init_mod
        spec.loader.exec_module(init_mod)

    ports_root = _PLUGIN_ROOT / "inbound_reply_ports"
    bundle_key = f"{_PKG_NAME}.internal.dispatch_context_bundle"
    if bundle_key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            bundle_key, _PLUGIN_ROOT / "internal" / "dispatch_context_bundle.py",
        )
        assert spec is not None and spec.loader is not None
        bundle_mod = importlib.util.module_from_spec(spec)
        sys.modules[bundle_key] = bundle_mod
        spec.loader.exec_module(bundle_mod)

    for name in ("in_process", "http"):
        key = f"{_PKG_NAME}.inbound_reply_ports.{name}"
        if key in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(key, ports_root / f"{name}.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)


@pytest.fixture()
def bridge_pkg():
    """Expose the loaded synthetic package (no DB) for pure-logic tests."""
    return _load_package()


@pytest.fixture()
def cal_db(tmp_path):
    """Point CAL at a fresh temp DB for the duration of one test."""
    pkg = _load_package()
    cal_mod = pkg.cal  # type: ignore[attr-defined]
    db_file = tmp_path / "cal.db"
    cal_mod.set_db_path(db_file)
    yield cal_mod
    cal_mod.set_db_path(None)
