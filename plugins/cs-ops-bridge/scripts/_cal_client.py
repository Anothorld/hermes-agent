"""HTTP client for cs-ops-bridge CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE = os.environ.get(
    "CS_OPS_BRIDGE_BASE",
    "http://127.0.0.1:8081/api/plugins/cs-ops-bridge",
).rstrip("/")
KEY_ENV = "HERMES_CS_OPS_BRIDGE_KEY"
SECRETS_PATH = Path(os.path.expanduser("~/.hermes/cs-ops-bridge/secrets.yaml"))
ENV_CHOICES = ("TEST", "LIVE")


def normalize_env(value: str) -> str:
    raw = (value or "").strip().upper()
    if raw in ENV_CHOICES:
        return raw
    raise argparse.ArgumentTypeError("env must be TEST or LIVE")


def _load_key() -> Optional[str]:
    for k in (KEY_ENV, "CS_OPS_BRIDGE_KEY"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    if SECRETS_PATH.exists():
        for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("bridge_key:"):
                val = line.split(":", 1)[1].strip().strip("'\"")
                if val:
                    return val
    # Gateway agent subprocess may not inherit bridge env; read profile .env.
    try:
        plugin_root = Path(__file__).resolve().parents[1]
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from profile_refs import cs_profile_dir  # noqa: WPS433

        env_path = cs_profile_dir() / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith(f"{KEY_ENV}="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        return val
    except Exception:
        pass
    return None


class BridgeClient:
    def __init__(self, base: str, key: Optional[str]) -> None:
        self.base = base.rstrip("/")
        self.key = key

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict[str, Any]] = None,
        query: Optional[dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base}{path}"
        if query:
            qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in query.items())
            url = f"{url}?{qs}"
        headers = {"Accept": "application/json"}
        if self.key:
            headers["X-Bridge-Key"] = self.key
        payload: Optional[bytes] = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            print(json.dumps({"error": err_body, "status": exc.code}), flush=True)
            sys.exit(2)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def client_from_args(args: argparse.Namespace) -> BridgeClient:
    return BridgeClient(getattr(args, "bridge_base", DEFAULT_BASE), getattr(args, "bridge_key", None) or _load_key())


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bridge-base", default=DEFAULT_BASE)
    p.add_argument("--bridge-key", default=None)


def add_env_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--env", type=normalize_env, required=True, choices=ENV_CHOICES)


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), flush=True)
