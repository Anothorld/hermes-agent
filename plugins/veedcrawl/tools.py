"""Tool schemas + handlers for the Veedcrawl plugin.

Each handler accepts the registry's ``arguments`` dict and returns a JSON
string built by ``tools.registry.tool_result`` / ``tool_error``. We keep the
client construction lazy and inside ``with`` blocks so HTTP connections are
released between agent turns.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tools.registry import tool_error, tool_result

from plugins.veedcrawl.client import VeedcrawlClient, resolve_api_key
from plugins.veedcrawl._internal.errors import VeedcrawlError

# --------------------------------------------------------------------- gating

def _check_veedcrawl_available() -> bool:
    """Return ``True`` iff an API key is configured via env."""
    return resolve_api_key() is not None


# --------------------------------------------------------------------- schemas

VEEDCRAWL_ACCOUNT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_health": {
            "type": "boolean",
            "description": "Also probe /health for service status.",
            "default": False,
        },
        "force_refresh": {
            "type": "boolean",
            "description": "Bypass the 60s /v1/me cache.",
            "default": False,
        },
    },
    "additionalProperties": False,
}

VEEDCRAWL_METADATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Public video URL (YouTube / TikTok / IG / X / Facebook).",
        },
        "force_refresh": {
            "type": "boolean",
            "description": "Bypass the 24h cache.",
            "default": False,
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}

VEEDCRAWL_TRANSCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Transcribe a public video. Provide ``url`` to start a new job, "
        "OR provide ``job_id`` alone to fetch the result of an existing job."
    ),
    "properties": {
        "url": {
            "type": "string",
            "description": "Public video URL. Required unless ``job_id`` is provided.",
        },
        "mode": {
            "type": "string",
            "enum": ["native", "generate", "auto"],
            "default": "auto",
            "description": (
                "native: free captions only (1 credit). "
                "generate: Whisper (5 credits). "
                "auto: prefer native, fall back to Whisper."
            ),
        },
        "lang": {
            "type": "string",
            "description": "ISO-639-1 hint (e.g. 'en', 'zh').",
        },
        "wait": {
            "type": "boolean",
            "default": True,
            "description": "If false, return jobId immediately for later polling.",
        },
        "timeout_s": {
            "type": "number",
            "default": 180,
            "description": "Max seconds to poll before giving up.",
        },
        "force_refresh": {"type": "boolean", "default": False},
        "job_id": {
            "type": "string",
            "description": (
                "Existing job id. Pass alone to fetch a previously submitted "
                "transcript result without spending new credits."
            ),
        },
    },
    "oneOf": [
        {"required": ["url"]},
        {"required": ["job_id"]},
    ],
    "additionalProperties": False,
}

VEEDCRAWL_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Run a structured extraction over a public video. Provide ``url`` and "
        "``prompt`` to start a new job, OR provide ``job_id`` alone to fetch "
        "the result of an existing job."
    ),
    "properties": {
        "url": {
            "type": "string",
            "description": "Public video URL. Required unless ``job_id`` is provided.",
        },
        "prompt": {
            "type": "string",
            "description": (
                "Natural-language extraction instructions. Required unless "
                "``job_id`` is provided."
            ),
        },
        "schema": {
            "type": "object",
            "description": "Optional JSON Schema constraining the response.",
        },
        "lang": {"type": "string", "description": "ISO-639-1 hint."},
        "wait": {"type": "boolean", "default": True},
        "timeout_s": {"type": "number", "default": 180},
        "force_refresh": {"type": "boolean", "default": False},
        "job_id": {
            "type": "string",
            "description": (
                "Existing job id. Pass alone to fetch a previously submitted "
                "extraction result without spending new credits."
            ),
        },
    },
    "oneOf": [
        {"required": ["url", "prompt"]},
        {"required": ["job_id"]},
    ],
    "additionalProperties": False,
}

VEEDCRAWL_JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Look up the result of a previously submitted Veedcrawl async job. "
        "Use this when an earlier ``veedcrawl_transcript`` or "
        "``veedcrawl_extract`` call returned a ``job_id``."
    ),
    "properties": {
        "endpoint": {
            "type": "string",
            "enum": ["transcript", "extract"],
            "description": "Which async endpoint produced the job.",
        },
        "job_id": {
            "type": "string",
            "description": "The ``job_id`` returned by the original tool call.",
        },
    },
    "required": ["endpoint", "job_id"],
    "additionalProperties": False,
}

VEEDCRAWL_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "platform": {
            "type": "string",
            "enum": ["instagram", "tiktok"],
            "description": "Profile data source.",
        },
        "username": {
            "type": "string",
            "description": "Handle without leading @ (mutually exclusive with url).",
        },
        "url": {
            "type": "string",
            "description": "Profile URL (mutually exclusive with username).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 12,
            "description": "Max recent posts to include.",
        },
        "force_refresh": {"type": "boolean", "default": False},
    },
    "required": ["platform"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- helpers

def _wrap_errors(fn: Callable[[VeedcrawlClient, dict[str, Any]], dict[str, Any]]) -> Callable[..., str]:
    """Convert ``VeedcrawlError`` raises into ``tool_error`` JSON."""

    def _runner(arguments: dict[str, Any] | None = None, **_: Any) -> str:
        args = dict(arguments or {})
        try:
            with VeedcrawlClient() as client:
                payload = fn(client, args)
        except VeedcrawlError as exc:
            return tool_error(str(exc), **exc.to_payload())
        except (TypeError, ValueError) as exc:
            return tool_error(str(exc), code="bad_request")
        except Exception as exc:  # pragma: no cover - defensive
            return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")
        return tool_result(payload)

    return _runner


# --------------------------------------------------------------------- handlers

@_wrap_errors
def _handle_account(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    me = client.me(force=bool(args.get("force_refresh")))
    out: dict[str, Any] = {"me": me}
    if bool(args.get("include_health")):
        out["health"] = client.health()
    return out


def _require(args: dict[str, Any], key: str, *, hint: str = "") -> str:
    """Return ``args[key]`` as ``str`` or raise ``ValueError`` (-> bad_request)."""
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        suffix = f" {hint}" if hint else ""
        raise ValueError(f"missing required argument {key!r}.{suffix}")
    return str(value)


@_wrap_errors
def _handle_metadata(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    url = _require(
        args,
        "url",
        hint="veedcrawl_metadata fetches video facts by URL only; it does not accept job_id.",
    )
    return client.metadata(
        url=url,
        force_refresh=bool(args.get("force_refresh")),
    )


@_wrap_errors
def _handle_transcript(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    job_id = args.get("job_id")
    if job_id:
        return client.lookup_job(endpoint="transcript", job_id=str(job_id))
    url = _require(
        args,
        "url",
        hint="Provide either url=<video URL> to start a new job or job_id=<id> to fetch an existing one.",
    )
    return client.transcript(
        url=url,
        mode=str(args.get("mode") or "auto"),
        lang=args.get("lang"),
        wait=bool(args.get("wait", True)),
        timeout_s=float(args.get("timeout_s") or 180.0),
        force_refresh=bool(args.get("force_refresh")),
    )


@_wrap_errors
def _handle_extract(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    job_id = args.get("job_id")
    if job_id:
        return client.lookup_job(endpoint="extract", job_id=str(job_id))
    schema = args.get("schema")
    if isinstance(schema, str):
        # Tolerate stringified JSON Schemas from less-strict callers.
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError as exc:
            raise ValueError(f"schema must be JSON Schema object, not string: {exc}") from exc
    url = _require(
        args,
        "url",
        hint="Provide either url+prompt to start a new job or job_id=<id> to fetch an existing one.",
    )
    prompt = _require(
        args,
        "prompt",
        hint="Provide either url+prompt to start a new job or job_id=<id> to fetch an existing one.",
    )
    return client.extract(
        url=url,
        prompt=prompt,
        schema=schema,
        lang=args.get("lang"),
        wait=bool(args.get("wait", True)),
        timeout_s=float(args.get("timeout_s") or 180.0),
        force_refresh=bool(args.get("force_refresh")),
    )


@_wrap_errors
def _handle_job(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    endpoint = _require(args, "endpoint")
    job_id = _require(args, "job_id")
    return client.lookup_job(endpoint=endpoint, job_id=job_id)


@_wrap_errors
def _handle_profile(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.profile(
        platform=str(args["platform"]),
        username=args.get("username"),
        url=args.get("url"),
        limit=int(args.get("limit") or 12),
        force_refresh=bool(args.get("force_refresh")),
    )


# Public exports consumed by ``__init__.register``.
__all__ = (
    "VEEDCRAWL_ACCOUNT_SCHEMA",
    "VEEDCRAWL_METADATA_SCHEMA",
    "VEEDCRAWL_TRANSCRIPT_SCHEMA",
    "VEEDCRAWL_EXTRACT_SCHEMA",
    "VEEDCRAWL_PROFILE_SCHEMA",
    "VEEDCRAWL_JOB_SCHEMA",
    "_handle_account",
    "_handle_metadata",
    "_handle_transcript",
    "_handle_extract",
    "_handle_profile",
    "_handle_job",
    "_check_veedcrawl_available",
)
