"""Tool schemas + handlers for the Veedcrawl plugin.

Each handler accepts the registry's ``arguments`` dict and returns a JSON
string built by ``tools.registry.tool_result`` / ``tool_error``. We keep the
client construction lazy and inside ``with`` blocks so HTTP connections are
released between agent turns.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from tools.registry import tool_error, tool_result

from plugins.veedcrawl.client import VeedcrawlClient, resolve_api_key
from plugins.veedcrawl._internal.bridge_persist import fetch_with_persist
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

_PERSIST_SCHEMA_PROPS: dict[str, Any] = {
    "env": {
        "type": "string",
        "enum": ["TEST", "LIVE"],
        "default": "LIVE",
        "description": "CAL / audit environment for optional identity facts.",
    },
    "identity_id": {
        "type": "integer",
        "description": "When set, write identity.veedcrawl_* index facts to CAL.",
    },
    "handle": {
        "type": "string",
        "description": "IG handle for CAL fact attribution (optional).",
    },
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
            "description": "Bypass the monthly persist cache.",
            "default": False,
        },
        **_PERSIST_SCHEMA_PROPS,
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
        **_PERSIST_SCHEMA_PROPS,
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
    "description": (
        "Fetch IG or TikTok profile + recent posts. Requires platform and "
        "either username or url — never call with an empty object."
    ),
    "properties": {
        "platform": {
            "type": "string",
            "enum": ["instagram", "tiktok"],
            "description": "Profile data source (required).",
        },
        "username": {
            "type": "string",
            "minLength": 1,
            "description": "Handle without leading @ (required unless url is set).",
        },
        "url": {
            "type": "string",
            "minLength": 1,
            "description": "Profile URL (required unless username is set).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 24,
            "default": 12,
            "description": "Max recent posts to include (REST hard cap 24).",
        },
        "force_refresh": {"type": "boolean", "default": False},
        **_PERSIST_SCHEMA_PROPS,
    },
    "allOf": [
        {"required": ["platform"]},
        {"oneOf": [{"required": ["username"]}, {"required": ["url"]}]},
    ],
    "additionalProperties": False,
}

VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Fetch Instagram profile + recent posts. Requires username or url — "
        "never call with an empty object."
    ),
    "properties": {
        "username": {
            "type": "string",
            "minLength": 1,
            "description": "IG handle without @ (required unless url is set).",
        },
        "url": {
            "type": "string",
            "minLength": 1,
            "description": "Profile URL (required unless username is set).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 24,
            "default": 12,
            "description": "Max recent posts (REST hard cap 24).",
        },
        "force_refresh": {"type": "boolean", "default": False},
        **_PERSIST_SCHEMA_PROPS,
    },
    "oneOf": [
        {"required": ["username"]},
        {"required": ["url"]},
    ],
    "additionalProperties": False,
}

VEEDCRAWL_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Search public social videos. Requires non-empty q.",
    "properties": {
        "q": {
            "type": "string",
            "minLength": 1,
            "description": "Search query (buyer-moment or cross-vertical phrase). Required.",
        },
        "platform": {
            "type": "string",
            "description": "Optional platform filter, e.g. instagram.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 6,
            "description": "Max results (REST hard cap 20).",
        },
        "force_refresh": {"type": "boolean", "default": False},
        **_PERSIST_SCHEMA_PROPS,
    },
    "required": ["q"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- helpers

def _job_lookup_envelope(
    *,
    operation: str,
    job_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Normalize async job lookup into the discovery persist envelope shape."""
    return {
        "ok": True,
        "operation": operation,
        "cache_month": None,
        "cache_key": f"job:{job_id}",
        "cache_hit": False,
        "api_calls": 0,
        "persisted": False,
        "blob_ref": None,
        "storage_ref": None,
        "identity_facts_written": False,
        "response": response,
        "job_lookup": True,
    }


def _persist_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "env": str(args.get("env") or "LIVE"),
        "force_refresh": bool(args.get("force_refresh")),
    }
    identity_id = args.get("identity_id")
    if identity_id is not None:
        out["identity_id"] = int(identity_id)
    handle = args.get("handle")
    if isinstance(handle, str) and handle.strip():
        out["handle"] = handle.strip().lstrip("@")
    return out


def _wrap_persisted(
    operation: str,
    request_builder: Callable[[dict[str, Any]], dict[str, Any]],
    fetch_builder: Callable[[VeedcrawlClient, dict[str, Any]], Any],
) -> Callable[..., str]:
    """Run fetch_with_persist and return the unified envelope."""

    def _runner(arguments: dict[str, Any] | None = None, **_: Any) -> str:
        args = dict(arguments or {})
        try:
            request = request_builder(args)
            with VeedcrawlClient() as client:
                envelope = fetch_with_persist(
                    operation=operation,
                    request=request,
                    fetch_fn=lambda: fetch_builder(client, args),
                    **_persist_kwargs(args),
                )
        except VeedcrawlError as exc:
            return tool_error(str(exc), **exc.to_payload())
        except (TypeError, ValueError) as exc:
            return tool_error(str(exc), code="bad_request")
        except Exception as exc:  # pragma: no cover - defensive
            return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")
        if not envelope.get("ok"):
            return tool_error(
                str(envelope.get("error") or "veedcrawl fetch failed"),
                code="upstream_error",
                persisted=envelope.get("persisted"),
            )
        return tool_result(envelope)

    return _runner


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


def _metadata_request(args: dict[str, Any]) -> dict[str, Any]:
    return {"url": _require(args, "url", hint="veedcrawl_metadata requires url.")}


def _metadata_fetch(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.metadata(
        url=str(args["url"]),
        force_refresh=True,
    )


_handle_metadata = _wrap_persisted(
    "get_video_metadata",
    _metadata_request,
    _metadata_fetch,
)


def _handle_transcript(arguments: dict[str, Any] | None = None, **_: Any) -> str:
    args = dict(arguments or {})
    try:
        job_id = args.get("job_id")
        if job_id:
            with VeedcrawlClient() as client:
                payload = client.lookup_job(endpoint="transcript", job_id=str(job_id))
            return tool_result(
                _job_lookup_envelope(
                    operation="get_video_transcript",
                    job_id=str(job_id),
                    response=payload,
                )
            )
        url = _require(
            args,
            "url",
            hint="Provide either url=<video URL> to start a new job or job_id=<id> to fetch an existing one.",
        )
        with VeedcrawlClient() as client:
            payload = client.transcript(
                url=url,
                mode=str(args.get("mode") or "auto"),
                lang=args.get("lang"),
                wait=bool(args.get("wait", True)),
                timeout_s=float(args.get("timeout_s") or 180.0),
                force_refresh=bool(args.get("force_refresh")),
            )
    except VeedcrawlError as exc:
        return tool_error(str(exc), **exc.to_payload())
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc), code="bad_request")
    except Exception as exc:  # pragma: no cover
        return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")
    return tool_result(payload)


def _parse_extract_schema(args: dict[str, Any]) -> Optional[dict[str, Any]]:
    schema = args.get("schema")
    if schema is None:
        return None
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError as exc:
            raise ValueError(f"schema must be JSON Schema object, not string: {exc}") from exc
    return schema if isinstance(schema, dict) else None


def _handle_extract(arguments: dict[str, Any] | None = None, **_: Any) -> str:
    args = dict(arguments or {})
    try:
        job_id = args.get("job_id")
        if job_id:
            with VeedcrawlClient() as client:
                payload = client.lookup_job(endpoint="extract", job_id=str(job_id))
            return tool_result(
                _job_lookup_envelope(
                    operation="extract_from_video",
                    job_id=str(job_id),
                    response=payload,
                )
            )
        schema = _parse_extract_schema(args)
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
        request = {"url": url, "prompt": prompt, "schema": schema, "lang": args.get("lang")}

        def _fetch(client: VeedcrawlClient, _args: dict[str, Any]) -> dict[str, Any]:
            return client.extract(
                url=url,
                prompt=prompt,
                schema=schema,
                lang=args.get("lang"),
                wait=bool(args.get("wait", True)),
                timeout_s=float(args.get("timeout_s") or 180.0),
                force_refresh=True,
            )

        with VeedcrawlClient() as client:
            envelope = fetch_with_persist(
                operation="extract_from_video",
                request=request,
                fetch_fn=lambda: _fetch(client, args),
                **_persist_kwargs(args),
            )
    except VeedcrawlError as exc:
        return tool_error(str(exc), **exc.to_payload())
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc), code="bad_request")
    except Exception as exc:  # pragma: no cover
        return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")
    if not envelope.get("ok"):
        return tool_error(
            str(envelope.get("error") or "veedcrawl extract failed"),
            code="upstream_error",
            persisted=envelope.get("persisted"),
        )
    return tool_result(envelope)


_JOB_ENDPOINT_OPERATIONS: dict[str, str] = {
    "extract": "extract_from_video",
    "transcript": "get_video_transcript",
}


def _handle_job(arguments: dict[str, Any] | None = None, **_: Any) -> str:
    args = dict(arguments or {})
    try:
        endpoint = _require(args, "endpoint")
        job_id = _require(args, "job_id")
        with VeedcrawlClient() as client:
            payload = client.lookup_job(endpoint=endpoint, job_id=job_id)
        operation = _JOB_ENDPOINT_OPERATIONS.get(endpoint, f"job_{endpoint}")
        return tool_result(
            _job_lookup_envelope(
                operation=operation,
                job_id=job_id,
                response=payload,
            )
        )
    except VeedcrawlError as exc:
        return tool_error(str(exc), **exc.to_payload())
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc), code="bad_request")
    except Exception as exc:  # pragma: no cover
        return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")


def _profile_request(args: dict[str, Any]) -> dict[str, Any]:
    platform = str(args.get("platform") or "").lower()
    if platform not in {"instagram", "tiktok"}:
        raise ValueError("platform must be instagram or tiktok")
    if not (args.get("username") or args.get("url")):
        raise ValueError("veedcrawl_profile requires platform and (username or url)")
    return {
        "platform": platform,
        "username": args.get("username"),
        "url": args.get("url"),
        "limit": int(args.get("limit") or 12),
    }


def _profile_fetch(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.profile(
        platform=str(args["platform"]),
        username=args.get("username"),
        url=args.get("url"),
        limit=int(args.get("limit") or 12),
        force_refresh=True,
    )


def _handle_profile(arguments: dict[str, Any] | None = None, **_: Any) -> str:
    args = dict(arguments or {})
    try:
        request = _profile_request(args)
        platform = request["platform"]
        operation = (
            "get_instagram_profile" if platform == "instagram" else "get_tiktok_profile"
        )
        with VeedcrawlClient() as client:
            envelope = fetch_with_persist(
                operation=operation,
                request=request,
                fetch_fn=lambda: _profile_fetch(client, args),
                **_persist_kwargs(args),
            )
    except VeedcrawlError as exc:
        return tool_error(str(exc), **exc.to_payload())
    except (TypeError, ValueError) as exc:
        return tool_error(str(exc), code="bad_request")
    except Exception as exc:  # pragma: no cover
        return tool_error(f"unexpected veedcrawl error: {exc}", code="internal_error")
    if not envelope.get("ok"):
        return tool_error(
            str(envelope.get("error") or "veedcrawl profile failed"),
            code="upstream_error",
            persisted=envelope.get("persisted"),
        )
    return tool_result(envelope)


def _instagram_profile_request(args: dict[str, Any]) -> dict[str, Any]:
    if not (args.get("username") or args.get("url")):
        raise ValueError("veedcrawl_instagram_profile requires username or url")
    return {
        "username": args.get("username"),
        "url": args.get("url"),
        "limit": int(args.get("limit") or 12),
    }


def _instagram_profile_fetch(client: VeedcrawlClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.profile(
        platform="instagram",
        username=args.get("username"),
        url=args.get("url"),
        limit=int(args.get("limit") or 12),
        force_refresh=True,
    )


_handle_instagram_profile = _wrap_persisted(
    "get_instagram_profile",
    _instagram_profile_request,
    _instagram_profile_fetch,
)


def _search_request(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "q": _require(args, "q"),
        "platform": args.get("platform"),
        "limit": int(args.get("limit") or 6),
    }


def _search_fetch(client: VeedcrawlClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    return client.search_social_videos(
        q=str(args["q"]),
        platform=args.get("platform"),
        limit=int(args.get("limit") or 6),
        force_refresh=True,
    )


_handle_search = _wrap_persisted(
    "search_social_videos",
    _search_request,
    _search_fetch,
)


# Public exports consumed by ``__init__.register``.
__all__ = (
    "VEEDCRAWL_ACCOUNT_SCHEMA",
    "VEEDCRAWL_METADATA_SCHEMA",
    "VEEDCRAWL_TRANSCRIPT_SCHEMA",
    "VEEDCRAWL_EXTRACT_SCHEMA",
    "VEEDCRAWL_PROFILE_SCHEMA",
    "VEEDCRAWL_INSTAGRAM_PROFILE_SCHEMA",
    "VEEDCRAWL_SEARCH_SCHEMA",
    "VEEDCRAWL_JOB_SCHEMA",
    "_handle_account",
    "_handle_metadata",
    "_handle_transcript",
    "_handle_extract",
    "_handle_profile",
    "_handle_instagram_profile",
    "_handle_search",
    "_handle_job",
    "_check_veedcrawl_available",
)
