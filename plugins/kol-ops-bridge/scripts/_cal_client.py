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

# Colloquial aliases agents/operators use; canonical partition keys are only TEST|LIVE.
_ENV_ALIASES: dict[str, str] = {
    "live": "LIVE",
    "prod": "LIVE",
    "production": "LIVE",
    "test": "TEST",
    "dev": "TEST",
}


def normalize_env(value: str) -> str:
    """Map ``--env`` to canonical ``TEST`` or ``LIVE`` (argparse ``type=`` hook)."""
    raw = (value or "").strip()
    if not raw:
        raise argparse.ArgumentTypeError(
            "env is required — use TEST (sandbox) or LIVE (production data)",
        )
    key = raw.lower()
    if key in _ENV_ALIASES:
        canonical = _ENV_ALIASES[key]
        if canonical != raw:
            print(
                f"kol_bridge_tool: normalized --env {raw!r} -> {canonical}",
                file=sys.stderr,
            )
        return canonical
    raise argparse.ArgumentTypeError(
        f"invalid env {raw!r}: CAL only accepts TEST or LIVE "
        "(production data = LIVE, not 'prod'). "
        "Accepted aliases: prod/production -> LIVE; dev -> TEST.",
    )


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
        timeout: float = 120.0,
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
        extra_headers: Optional[dict[str, str]] = None,
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
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            fields: dict[str, Any] = {
                "status": exc.code,
                "detail": detail,
                "path": path,
            }
            hint = _http_error_hint(detail)
            if hint:
                fields["hint"] = hint
            _die("http_error", **fields)
        except urllib.error.URLError as exc:
            _die("bridge_unreachable", detail=str(exc.reason), base=self.base)
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {"_raw": payload.decode("utf-8", "replace")}


# ----------------------------------------------------------------- helpers
_JSON_FIELD_HINTS: dict[str, str] = {
    "identity_id": (
        "Integer CAL identity from get-dispatch-context or brief "
        "(flag --identity-id <id>; never --id)."
    ),
    "campaign_id": "Campaign id string from brief (e.g. SEB8010-20260608).",
    "child_skill": (
        "Skill name in JSON (e.g. kol-cold-outreach). Required for "
        "persist-initial-outreach-draft / persist-reply-draft payloads."
    ),
    "child_envelope": (
        "Draft object {subject, body, to, html?, kind?}. "
        "See kol-cold-outreach SKILL §3 persist example."
    ),
    "primary_handle": (
        "Instagram handle without @ (ingest-confirmed-candidate only)."
    ),
    "namespaces": (
        'Dict of namespace → facts, e.g. {"identity": {"identity.email_source": "ig_bio", ...}}.'
    ),
    "source_message_id": "Gmail message id or stable draft: anchor from dispatch context.",
    "primary_lane": "Usually commerce.",
    "primary_goal": "Goal from dispatch context (e.g. outreach, compensation_negotiation).",
    "latest_email": "Thread anchor {thread_id, message_id, subject}.",
}


def _http_error_hint(detail: str) -> Optional[str]:
    if "requires provenance keys" in detail:
        return (
            "Include provenance triple in the same write-facts-multi payload: "
            "<field>_source, <field>_discovered_at, <field>_discovered_url "
            "(see kol-email-discovery SKILL Step 5a side-effect table)."
        )
    return None


def _die(error: str, **fields: Any) -> "Any":  # noqa: ANN401 — raises
    """Print a stable JSON error and exit non-zero.

    The primary consumer is the Hermes agent, which only sees the terminal
    tool's **stdout**. Writing errors to stderr alone made every failure look
    like empty output (``{"output": "", "exit_code": 2}``), so the agent could
    not tell what went wrong and fell back to ad-hoc ``execute_code`` (POVISON
    recovery incident). Emit on stdout so the failure is always visible; mirror
    to stderr for human operators on a TTY.
    """
    payload = {"error": error, **fields}
    line = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    sys.stderr.write(line + "\n")
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
    """Attach the mandatory ``--env`` argument (canonical TEST|LIVE)."""
    p.add_argument(
        "--env",
        required=required,
        type=normalize_env,
        metavar="ENV",
        help=(
            "CAL partition: TEST (sandbox) or LIVE (production). "
            "Aliases prod/production -> LIVE; dev -> TEST. No default."
        ),
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
            hint = _JSON_FIELD_HINTS.get(k)
            if hint:
                _die("json_missing_field", field=k, hint=hint)
            else:
                _die("json_missing_field", field=k)


def print_json(out: Any) -> None:
    """Emit JSON on stdout (compact by default) and flush for pipe consumers."""
    if _JSON_PRETTY:
        text = json.dumps(out, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


_JSON_PRETTY = False


def set_json_output_mode(*, pretty: bool = False) -> None:
    """Configure CLI JSON formatting (``--pretty`` on kol_bridge_tool)."""
    global _JSON_PRETTY
    _JSON_PRETTY = pretty


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
    "set_json_output_mode",
]
