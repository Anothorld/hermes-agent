"""Validate conversation-context classification against real emails.

Fetches messages from QuickCEP via the bridge CLI, extracts body + conversation
history using the new intent_gate functions, runs the classifier, and compares
the new result to the prior classification stored in cs_intent.db.

Usage:
    cd plugins/cs-intent-classifier
    python3 scripts/validate_context.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_BRIDGE_ROOT = _PLUGIN_ROOT.parent / "cs-ops-bridge"

# ── Load cs-intent-classifier package ──
_PKG = "cs_intent_classifier_pkg"


def _load_pkg() -> ModuleType:
    if _PKG in sys.modules:
        return sys.modules[_PKG]
    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG] = pkg
    for sub in ("schemas", "db", "classifier", "plugin_api", "learning", "eval_runner"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG
        sys.modules[f"{_PKG}.{sub}"] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


_load_pkg()

# Load .env
_env_path = _PLUGIN_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from cs_intent_classifier_pkg import classifier  # type: ignore

# ── Load intent_gate functions from cs-ops-bridge ──
_BRIDGE_PKG = "cs_ops_bridge_for_validation"


def _load_bridge():
    if _BRIDGE_PKG in sys.modules:
        return sys.modules[_BRIDGE_PKG]
    pkg = ModuleType(_BRIDGE_PKG)
    pkg.__path__ = [str(_BRIDGE_ROOT)]
    sys.modules[_BRIDGE_PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{_BRIDGE_PKG}.intent_gate",
        _BRIDGE_ROOT / "intent_gate.py",
        submodule_search_locations=[str(_BRIDGE_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _BRIDGE_PKG
    sys.modules[f"{_BRIDGE_PKG}.intent_gate"] = mod
    spec.loader.exec_module(mod)
    setattr(pkg, "intent_gate", mod)
    return pkg


bridge = _load_bridge()
ig = bridge.intent_gate

CAL_DB = os.environ.get("CS_OPS_CAL_DB", "/Users/arnold/.hermes/cs-ops-bridge/cal.db")
INTENT_DB = str(_PLUGIN_ROOT / "data" / "cs_intent.db")
CLI = str(_BRIDGE_ROOT / "scripts" / "cs_bridge_tool.py")
ENV = "LIVE"


def fetch_messages(session_id: str) -> list:
    """Fetch messages via bridge CLI."""
    out = subprocess.run(
        ["python3", CLI, "get-messages", "--env", ENV, "--session-id", session_id],
        capture_output=True, text=True, timeout=10.0,
    )
    if out.returncode != 0:
        return []
    data = json.loads(out.stdout)
    return data.get("messages") or []


def get_prior_classification(session_id: str) -> dict | None:
    """Get the prior classification from cs_intent.db."""
    conn = sqlite3.connect(INTENT_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT gate_extract_json, classifier_source, classified_at "
        "FROM cs_intent_classifications WHERE session_id=? AND env=? "
        "ORDER BY classified_at DESC LIMIT 1",
        (session_id, ENV),
    ).fetchone()
    conn.close()
    if not row:
        return None
    ge = json.loads(row["gate_extract_json"])
    return {
        "primary_intent": ge.get("primary_intent"),
        "in_scope": ge.get("in_scope"),
        "summary_zh": ge.get("summary_zh", ""),
        "classifier_source": row["classifier_source"],
        "classified_at": row["classified_at"],
    }


def classify_with_context(session_id: str) -> dict:
    """Classify with conversation context using the new functions."""
    messages = fetch_messages(session_id)
    if not messages:
        return {"error": "no messages"}

    last = ig._latest_visitor_message(messages)
    if last is None:
        return {"error": "no visitor message"}

    body = ig._extract_message_text(last)
    subject = ""  # subject comes from watcher info, not messages
    history = ig._extract_conversation_history(messages, ig._context_turns())

    # Also strip quoted reply from the body being classified
    body_clean = ig._strip_quoted_reply(body)

    result = classifier.classify(
        subject=subject,
        body=body_clean,
        metadata={"customer_email": "", "env": ENV},
        conversation_history=history,
    )
    return {
        "primary_intent": result.get("primary_intent"),
        "in_scope": result.get("in_scope"),
        "summary_zh": result.get("summary_zh", ""),
        "classifier_source": result.get("classifier_source"),
        "is_conversation_closing": result.get("is_conversation_closing", False),
        "history_len": len(history),
        "history_roles": [h["role"] for h in history],
        "body_preview": body_clean[:200],
    }


def main():
    # Sessions to validate — mix of suspicious classifications + normal ones
    sessions = [
        "2551927504489046017",  # "扶手问题" classified as spam — suspicious
        "2551985989058707458",  # "系统自动发送" classified as spam
        "2551981466458177536",  # order_management — reply about payment
        "2551813400092631043",  # "客户确认已按照要求完成操作" — should be closing?
        "2552098134950125568",  # logistics_inquiry — normal, sanity check
        "2552373519663792128",  # "change order to mila" — not in prior db
        "2551846853592866817",  # cancellation thread — multi-reply
    ]

    print(f"{'Session':<22} {'Old':<18} {'New':<18} {'Hist':<6} {'Closing':<7} Summary")
    print("=" * 120)

    for sid in sessions:
        prior = get_prior_classification(sid)
        new = classify_with_context(sid)

        if "error" in new:
            print(f"{sid:<22} {prior['primary_intent'] if prior else '—':<18} ERROR: {new['error']}")
            continue

        old_pi = prior["primary_intent"] if prior else "(none)"
        new_pi = new["primary_intent"]
        hist = f"{new['history_len']}msg"
        closing = "YES" if new["is_conversation_closing"] else ""
        changed = " <<< CHANGED" if prior and old_pi != new_pi else ""

        print(f"{sid:<22} {old_pi:<18} {new_pi:<18} {hist:<6} {closing:<7} {new['summary_zh'][:50]}{changed}")

        if new["history_len"] > 0:
            for h in new["history_roles"]:
                pass  # compact mode
            # Show history context briefly
            # (re-fetch to show text)
            messages = fetch_messages(sid)
            history = ig._extract_conversation_history(messages, ig._context_turns())
            for h in history:
                role_label = "客户" if h["role"] == "customer" else "客服"
                print(f"  [{role_label}] {h['text'][:100]}")

        print()


if __name__ == "__main__":
    main()
