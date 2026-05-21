"""HTTP client for the ``kol-ops-bridge`` plugin (shared CLI core).

Used by:
- :mod:`kol_bridge_tool` — the deterministic CLI shipped to SKILLs.
- :mod:`kol_reply_dispatcher` — the gmail reply poller daemon.

Designed as a thin layer so all bridge HTTP traffic flows through one
place (single retry / auth / error-shape decision).  Subcommand modules
build paths + bodies; this client only knows how to send/receive JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode


DEFAULT_BASE = os.environ.get(
    "HERMES_KOL_OPS_BRIDGE_BASE",
    "http://127.0.0.1:8080/api/plugins/kol-ops-bridge",
).rstrip("/")
KEY_ENV = "HERMES_KOL_OPS_BRIDGE_KEY"
KEY_ENV_ALIASES = (
    KEY_ENV,
    "KOC_BRIDGE_KEY",
    "HERMES_KOL_BRIDGE_KEY",
    "BRIDGE_KEY",
)
SECRETS_PATH = Path(os.path.expanduser("~/.hermes/kol-ops-bridge/secrets.yaml"))
CONSOLE_ENV_PATH = (
    Path(__file__).resolve().parents[3] / "playground/kol-ops-console/.env"
)
ENV_CHOICES = ("TEST", "LIVE")


def _load_key_from_kv_file(path: Path, keys: tuple[str, ...]) -> Optional[str]:
    if not path.exists():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line and "=" not in line:
                continue
            sep = ":" if ":" in line else "="
            key, value = line.split(sep, 1)
            if key.strip() in keys and value.strip():
                return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def load_bridge_key(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve the bridge key from explicit arg, env aliases, or secrets.yaml."""
    if explicit and explicit.strip():
        return explicit.strip()
    for name in KEY_ENV_ALIASES:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return (
        _load_key_from_kv_file(SECRETS_PATH, ("bridge_key",))
        or _load_key_from_kv_file(CONSOLE_ENV_PATH, KEY_ENV_ALIASES)
    )


class CALClient:
    """Synchronous JSON client for the kol-ops-bridge HTTP API.

    Errors are raised as :class:`SystemExit` carrying a JSON-encoded
    ``{error, status, detail}`` payload so CLI callers (and the agent
    invoking the CLI) get a single, parseable failure shape.
    """

    def __init__(
        self,
        base: Optional[str] = None,
        bridge_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.bridge_key = load_bridge_key(bridge_key)
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        body: Optional[Any] = None,
    ) -> Any:
        url = f"{self.base}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean)}"
        data: Optional[bytes] = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.bridge_key:
            headers["X-Bridge-Key"] = self.bridge_key
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            _die("http_error", status=exc.code, detail=detail, path=path)
        except urllib.error.URLError as exc:
            _die("bridge_unreachable", detail=str(exc.reason), base=self.base)
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {"_raw": payload.decode("utf-8", "replace")}


# ----------------------------------------------------------------- helpers
def _die(error: str, **fields: Any) -> "Any":  # noqa: ANN401 — raises
    """Print a stable JSON error to stderr and exit non-zero."""
    payload = {"error": error, **fields}
    sys.stderr.write(json.dumps(payload, ensure_ascii=False))
    sys.stderr.write("\n")
    raise SystemExit(2)


def add_common_args(p: argparse.ArgumentParser) -> None:
    """Attach ``--base`` + ``--bridge-key`` to a subparser."""
    p.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"Bridge HTTP base URL (default %(default)s; env HERMES_KOL_OPS_BRIDGE_BASE)",
    )
    p.add_argument(
        "--bridge-key",
        default=None,
        help=(
            "X-Bridge-Key header value. Defaults to env "
            f"{KEY_ENV}, KOC_BRIDGE_KEY, HERMES_KOL_BRIDGE_KEY, BRIDGE_KEY, "
            "or ~/.hermes/kol-ops-bridge/secrets.yaml."
        ),
    )


def add_env_arg(p: argparse.ArgumentParser, *, required: bool = True) -> None:
    """Attach the mandatory ``--env`` choice argument."""
    p.add_argument(
        "--env",
        required=required,
        choices=ENV_CHOICES,
        help="TEST or LIVE — never defaults to avoid cross-env writes.",
    )


def client_from_args(args: argparse.Namespace) -> CALClient:
    return CALClient(base=getattr(args, "base", None),
                     bridge_key=getattr(args, "bridge_key", None))


def parse_json_arg(val: Optional[str], *, required: bool = True) -> dict[str, Any]:
    """Parse a ``--json`` argument or ``@path`` file reference into a dict.

    Accepts inline JSON or ``@/abs/path/to/file.json`` for large bodies.
    """
    if not val:
        if required:
            _die("missing_json")
        return {}
    if val.startswith("@"):
        try:
            with open(val[1:], "rb") as fh:
                val = fh.read().decode("utf-8")
        except OSError as exc:
            _die("json_file_read_failed", path=val[1:], detail=str(exc))
    try:
        out = json.loads(val)
    except json.JSONDecodeError as exc:
        _die("bad_json", detail=str(exc))
    if not isinstance(out, dict):
        _die("json_must_be_object")
    return out


def require_keys(body: dict[str, Any], *keys: str) -> None:
    for k in keys:
        if k not in body:
            _die("json_missing_field", field=k)


def print_json(out: Any) -> None:
    print(json.dumps(out, ensure_ascii=False, indent=2))


__all__ = [
    "CALClient",
    "DEFAULT_BASE",
    "KEY_ENV",
    "ENV_CHOICES",
    "add_common_args",
    "add_env_arg",
    "client_from_args",
    "parse_json_arg",
    "print_json",
    "require_keys",
]
