"""langfuse -- Hermes plugin for Langfuse observability.

Traces Hermes conversations, LLM calls, and tool usage to Langfuse.
Activation is handled by the Hermes plugin system -- standalone plugins only
load when listed in ``plugins.enabled`` (via ``hermes plugins enable
observability/langfuse``, or by checking the box in the interactive
``hermes plugins`` UI). At runtime the plugin also requires the
``langfuse`` SDK and credentials; if either is missing the hooks are inert.

Required env vars (set in ~/.hermes/.env):
  HERMES_LANGFUSE_PUBLIC_KEY  - Langfuse project public key (pk-lf-...)
  HERMES_LANGFUSE_SECRET_KEY  - Langfuse project secret key (sk-lf-...)
  HERMES_LANGFUSE_BASE_URL    - Langfuse server URL (default: https://cloud.langfuse.com)

Optional env vars:
  HERMES_LANGFUSE_ENV         - environment tag (e.g. "production", "local")
  HERMES_LANGFUSE_RELEASE     - release/version tag
  HERMES_LANGFUSE_USER_ID     - user identifier for cost attribution & filtering
  HERMES_LANGFUSE_SAMPLE_RATE - sampling rate 0.0-1.0 (default: 1.0)
  HERMES_LANGFUSE_MAX_CHARS   - max chars per field (default: 12000)
  HERMES_LANGFUSE_DEBUG       - set to "true" for verbose logging

Langfuse SDK v4 notes:
  - ``release`` and ``environment`` are set via LANGFUSE_RELEASE /
    LANGFUSE_TRACING_ENVIRONMENT env vars (not constructor kwargs).
  - ``metadata`` values must be ``dict[str, str]`` with values <=200 chars.
  - ``set_trace_io()`` is deprecated; set input/output on root observation directly.
  - ``should_export_span`` controls span export; default only exports LLM spans.
"""
_PLUGIN_VERSION = "2024-06-22-split-handlers"  # marker for runtime verification

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse, propagate_attributes
except Exception:  # pragma: no cover - fail-open when optional dep is missing
    Langfuse = None
    propagate_attributes = None


@dataclass
class TraceState:
    trace_id: str
    root_ctx: Any
    root_span: Any
    generations: Dict[str, Any] = field(default_factory=dict)
    tools: Dict[str, Any] = field(default_factory=dict)
    pending_tools_by_name: Dict[str, list] = field(default_factory=dict)
    turn_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    last_updated_at: float = field(default_factory=time.time)
    output_set: bool = False


_STATE_LOCK = threading.Lock()
_TRACE_STATE: Dict[str, TraceState] = {}
_LANGFUSE_CLIENT = None
_READ_FILE_LINE_RE = re.compile(r"^\s*(\d+)\|(.*)$")
_READ_FILE_HEAD_LINES = 25
_READ_FILE_TAIL_LINES = 15

# Langfuse-issued keys always carry these prefixes (cloud or self-hosted —
# the prefix is baked into the server-side issuance flow, not a UI hint).
# Anything else (`placeholder`, `test-key`, `your-langfuse-key`, etc.) is a
# leftover template value and would cause the SDK to silently accept the
# credentials at construction time but drop every trace at flush time.
# See #23823 — the silent-failure bug this guard fixes.
_LANGFUSE_KEY_PREFIXES: Dict[str, str] = {
    "HERMES_LANGFUSE_PUBLIC_KEY": "pk-lf-",
    "HERMES_LANGFUSE_SECRET_KEY": "sk-lf-",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(*names: str) -> bool:
    for name in names:
        value = _env(name).lower()
        if value:
            return value in {"1", "true", "yes", "on"}
    return False


def _debug_enabled() -> bool:
    return _env_bool("HERMES_LANGFUSE_DEBUG")


def _debug(message: str) -> None:
    if _debug_enabled():
        logger.info("Langfuse tracing: %s", message)


# Sentinel: "_get_langfuse() has tried and failed". Lets us short-circuit
# every subsequent hook call without re-checking env vars or re-attempting
# SDK init. Tests clear this by reloading the module via
# ``sys.modules.pop(...) + importlib.import_module(...)`` rather than via a
# dedicated reset function. Runtime callers cannot reset the cache; if an
# operator fixes a misconfigured credential they must restart the process.
_INIT_FAILED = object()


def _redact_key_preview(value: str) -> str:
    """Return a brief, log-safe preview of a credential value.

    Keeps enough characters to disambiguate common placeholders
    (``placeholder``, ``test-key``, ``your-key``) without echoing a
    real secret in full if an operator pasted one into the wrong env
    var.  Used only for the once-per-process placeholder-detection
    warning in :func:`_get_langfuse`.
    """
    if not value:
        return "<empty>"
    if len(value) <= 12:
        return repr(value)
    return repr(value[:6] + "...")


def _validate_langfuse_key(env_name: str, value: str) -> Optional[str]:
    """Return an error message if ``value`` is not a real Langfuse key.

    Returns ``None`` when the value matches the documented Langfuse
    prefix for ``env_name``, or when no prefix is registered for the
    name (in which case we trust the operator).  When validation
    fails the returned string is suitable for direct inclusion in a
    single log line — it names the env var and shows a safe preview.
    """
    expected = _LANGFUSE_KEY_PREFIXES.get(env_name, "")
    if not expected:
        return None
    if value.startswith(expected):
        return None
    return (
        f"{env_name}={_redact_key_preview(value)} "
        f"(expected {expected!r} prefix)"
    )


def _get_langfuse() -> Optional[Langfuse]:
    """Return a cached Langfuse client, or ``None`` if unavailable.

    Activation of this plugin is controlled by the Hermes plugin system —
    this function only handles the runtime-availability gate (SDK installed
    + credentials present). The result is cached: on the first call we try
    to construct a client, and every subsequent call returns that client
    (or fast-returns ``None`` if init failed).
    """
    global _LANGFUSE_CLIENT
    if _LANGFUSE_CLIENT is _INIT_FAILED:
        return None
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT

    if Langfuse is None:
        _LANGFUSE_CLIENT = _INIT_FAILED
        return None

    public_key = _env("HERMES_LANGFUSE_PUBLIC_KEY") or _env("LANGFUSE_PUBLIC_KEY")
    secret_key = _env("HERMES_LANGFUSE_SECRET_KEY") or _env("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        _LANGFUSE_CLIENT = _INIT_FAILED
        return None

    # Reject placeholder credentials with a one-shot warning so the
    # operator sees the misconfiguration instead of silently shipping a
    # broken observability stack (#23823).  The SDK does not validate
    # keys at construction time — it queues traces in memory and only
    # discovers the auth failure when the background flush thread tries
    # to post them, by which point the warning is buried under whatever
    # else the process is logging.  Catch it here, surface it once, and
    # short-circuit via the same _INIT_FAILED path as the empty case.
    placeholder_issues = [
        msg
        for msg in (
            _validate_langfuse_key("HERMES_LANGFUSE_PUBLIC_KEY", public_key),
            _validate_langfuse_key("HERMES_LANGFUSE_SECRET_KEY", secret_key),
        )
        if msg
    ]
    if placeholder_issues:
        logger.warning(
            "Langfuse plugin: credentials look like placeholders, traces will "
            "NOT be emitted (%s). Set real Langfuse keys (pk-lf-... / sk-lf-...) "
            "or unset HERMES_LANGFUSE_PUBLIC_KEY / HERMES_LANGFUSE_SECRET_KEY to "
            "silence this warning.",
            "; ".join(placeholder_issues),
        )
        _LANGFUSE_CLIENT = _INIT_FAILED
        return None

    base_url = _env("HERMES_LANGFUSE_BASE_URL") or _env("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com"

    # In Langfuse SDK v4, the OpenTelemetry-based exporter reads credentials
    # from LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST env vars
    # (NOT constructor kwargs). The HERMES_ prefixed vars are our convention;
    # we must bridge them to the SDK-expected env vars so the OTLP exporter
    # can authenticate. Without this, the exporter gets 401 Unauthorized.
    if public_key and not _env("LANGFUSE_PUBLIC_KEY"):
        os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    if secret_key and not _env("LANGFUSE_SECRET_KEY"):
        os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    if base_url and not _env("LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = base_url

    # Release and environment are also read from env vars in v4.
    if _env("HERMES_LANGFUSE_RELEASE") and not _env("LANGFUSE_RELEASE"):
        os.environ["LANGFUSE_RELEASE"] = _env("HERMES_LANGFUSE_RELEASE")
    if _env("HERMES_LANGFUSE_ENV") and not _env("LANGFUSE_TRACING_ENVIRONMENT"):
        os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = _env("HERMES_LANGFUSE_ENV")

    sample_rate = _env("HERMES_LANGFUSE_SAMPLE_RATE")

    kwargs: Dict[str, Any] = {
        "public_key": public_key,
        "secret_key": secret_key,
        "base_url": base_url,
    }
    if sample_rate:
        try:
            kwargs["sample_rate"] = float(sample_rate)
        except ValueError:
            logger.warning("Invalid HERMES_LANGFUSE_SAMPLE_RATE=%r", sample_rate)

    try:
        _LANGFUSE_CLIENT = Langfuse(**kwargs)
    except Exception as exc:  # pragma: no cover - fail-open
        logger.warning("Could not initialize Langfuse client: %s", exc)
        _LANGFUSE_CLIENT = _INIT_FAILED
        return None

    return _LANGFUSE_CLIENT


def _get_user_id() -> str:
    """Return the user identifier for Langfuse traces.

    Checked in order: HERMES_LANGFUSE_USER_ID, LANGFUSE_USER_ID, then
    the Hermes USER.md profile name. This enables per-user cost
    attribution and filtering in the Langfuse dashboard.
    """
    explicit = _env("HERMES_LANGFUSE_USER_ID") or _env("LANGFUSE_USER_ID")
    if explicit:
        return explicit
    # Try to read user name from Hermes user profile
    try:
        from agent.config import get_user_name
        name = get_user_name()
        if name:
            return str(name)
    except Exception:
        pass
    return ""


def _trace_key(task_id: str, session_id: str) -> str:
    if task_id:
        return task_id
    if session_id:
        return f"session:{session_id}"
    return f"thread:{threading.get_ident()}"


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"


def _maybe_parse_json_string(value: str) -> Any:
    stripped = value.strip()
    if len(stripped) < 2 or stripped[0] not in "{[" or stripped[-1] not in "}]":
        if len(stripped) < 2 or stripped[0] not in "{[":
            return value
    try:
        parsed, idx = json.JSONDecoder().raw_decode(stripped)
    except Exception:
        return value
    if not isinstance(parsed, (dict, list)):
        return value

    trailing = stripped[idx:].strip()
    if not trailing:
        return parsed

    hint_key = "_hint" if trailing.startswith("[Hint:") else "_trailing_text"
    if isinstance(parsed, dict):
        merged = dict(parsed)
        key = hint_key if hint_key not in merged else "_trailing_text"
        merged[key] = trailing
        return merged

    return {"data": parsed, hint_key: trailing}


def _looks_like_read_file_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    content = value.get("content")
    return (
        isinstance(content, str)
        and "total_lines" in value
        and "file_size" in value
        and "is_binary" in value
        and "is_image" in value
        and not value.get("error")
    )


def _parse_read_file_lines(content: str) -> list[dict[str, Any]]:
    if not isinstance(content, str) or not content:
        return []

    lines = []
    for raw_line in content.splitlines():
        match = _READ_FILE_LINE_RE.match(raw_line)
        if not match:
            return []
        lines.append({
            "line": int(match.group(1)),
            "text": match.group(2),
        })
    return lines


def _build_read_file_preview(lines: list[dict[str, Any]]) -> dict[str, Any]:
    if len(lines) <= (_READ_FILE_HEAD_LINES + _READ_FILE_TAIL_LINES):
        return {"lines": lines}

    return {
        "head": lines[:_READ_FILE_HEAD_LINES],
        "tail": lines[-_READ_FILE_TAIL_LINES:],
        "omitted_line_count": len(lines) - _READ_FILE_HEAD_LINES - _READ_FILE_TAIL_LINES,
    }


def _normalize_read_file_payload(value: dict[str, Any], *, args: Any = None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if isinstance(args, dict):
        path = args.get("path")
        offset = args.get("offset")
        limit = args.get("limit")
        if isinstance(path, str) and path:
            normalized["path"] = path
        if isinstance(offset, int):
            normalized["offset"] = offset
        if isinstance(limit, int):
            normalized["limit"] = limit

    lines = _parse_read_file_lines(value.get("content", ""))
    if lines:
        normalized["returned_lines"] = {
            "start": lines[0]["line"],
            "end": lines[-1]["line"],
            "count": len(lines),
        }
        normalized["content_preview"] = _build_read_file_preview(lines)
    elif value.get("content"):
        normalized["content_preview"] = {
            "text": value.get("content", ""),
        }

    for key in (
        "total_lines",
        "file_size",
        "truncated",
        "is_binary",
        "is_image",
        "hint",
        "_warning",
        "mime_type",
        "dimensions",
        "similar_files",
        "error",
    ):
        if key in value:
            normalized[key] = value[key]

    base64_content = value.get("base64_content")
    if isinstance(base64_content, str) and base64_content:
        normalized["base64_content"] = {
            "omitted": True,
            "length": len(base64_content),
        }

    return normalized


def _normalize_payload(value: Any, *, tool_name: str = "", args: Any = None) -> Any:
    if _looks_like_read_file_payload(value):
        return _normalize_read_file_payload(
            value,
            args=args if tool_name == "read_file" else None,
        )
    return value


def _safe_value(value: Any, *, max_chars: Optional[int] = None, depth: int = 0,
                parse_json_strings: bool = False) -> Any:
    max_chars = max_chars if max_chars is not None else int(_env("HERMES_LANGFUSE_MAX_CHARS", "12000") or "12000")
    if depth > 4:
        return "<max-depth>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "len": len(value)}
    if isinstance(value, str):
        if parse_json_strings:
            parsed = _maybe_parse_json_string(value)
            if parsed is not value:
                return _safe_value(parsed, max_chars=max_chars, depth=depth, parse_json_strings=True)
        return _truncate_text(value, max_chars)
    if isinstance(value, dict):
        normalized = _normalize_payload(value)
        if normalized is not value:
            return _safe_value(normalized, max_chars=max_chars, depth=depth, parse_json_strings=parse_json_strings)
        return {
            str(k): _safe_value(v, max_chars=max_chars, depth=depth + 1, parse_json_strings=parse_json_strings)
            for k, v in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _safe_value(v, max_chars=max_chars, depth=depth + 1, parse_json_strings=parse_json_strings)
            for v in list(value)[:50]
        ]
    if hasattr(value, "__dict__"):
        return _safe_value(vars(value), max_chars=max_chars, depth=depth + 1, parse_json_strings=parse_json_strings)
    return _truncate_text(repr(value), max_chars)


def _extract_last_user_message(messages: Any) -> Any:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return {
                "role": "user",
                "content": _safe_value(message.get("content")),
            }
    return None


def _coerce_request_messages(
    *,
    request_messages: Any = None,
    messages: Any = None,
    conversation_history: Any = None,
    user_message: Any = None,
) -> list[dict[str, Any]]:
    for candidate in (request_messages, messages, conversation_history):
        if isinstance(candidate, list):
            return candidate
    if user_message is None:
        return []
    return [{"role": "user", "content": user_message}]


def _serialize_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    serialized = []
    for message in messages[-12:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        item = {
            "role": role,
            "content": _safe_value(
                message.get("content"),
                parse_json_strings=(role == "tool"),
            ),
        }
        if role == "tool":
            if message.get("tool_call_id"):
                item["tool_call_id"] = message.get("tool_call_id")
            if message.get("name"):
                item["name"] = _safe_value(message.get("name"))
        if message.get("tool_calls"):
            item["tool_calls"] = _safe_value(message.get("tool_calls"), parse_json_strings=True)
        serialized.append(item)
    return serialized


def _serialize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    serialized = []
    for tool_call in tool_calls:
        fn = getattr(tool_call, "function", None)
        name = getattr(fn, "name", None) if fn else None
        arguments = getattr(fn, "arguments", None) if fn else None
        safe_arguments = _safe_value(arguments, parse_json_strings=False)
        serialized.append({
            "id": getattr(tool_call, "id", None),
            "type": getattr(tool_call, "type", None) or "function",
            "name": name,
            "arguments": safe_arguments,
            "function": {
                "name": name,
                "arguments": safe_arguments,
            },
        })
    return serialized


def _serialize_assistant_message(message: Any) -> dict[str, Any]:
    return {
        "content": _safe_value(getattr(message, "content", None)),
        "reasoning": _safe_value(getattr(message, "reasoning", None)),
        "tool_calls": _serialize_tool_calls(getattr(message, "tool_calls", None)),
    }


def _usage_and_cost(response: Any, *, provider: str, api_mode: str, model: str, base_url: str) -> tuple[dict[str, int], dict[str, float]]:
    usage_details: Dict[str, int] = {}
    cost_details: Dict[str, float] = {}
    raw_usage = getattr(response, "usage", None)
    if not raw_usage:
        return usage_details, cost_details

    try:
        from agent.usage_pricing import estimate_usage_cost, normalize_usage

        canonical = normalize_usage(raw_usage, provider=provider, api_mode=api_mode)
        # Langfuse usage_details keys follow a naming convention:
        #   - Dashboard sums all keys containing "input" as input total
        #   - Dashboard sums all keys containing "output" as output total
        #   - If no "total" key, Langfuse derives it from all usage types
        # Use Anthropic-style key names so cache tokens roll into the
        # dashboard input total automatically.
        # Ref: https://langfuse.com/docs/model-usage-and-cost
        usage_details = {
            "input": canonical.input_tokens,
            "output": canonical.output_tokens,
        }
        if canonical.cache_read_tokens:
            usage_details["cache_read_input_tokens"] = canonical.cache_read_tokens
        if canonical.cache_write_tokens:
            usage_details["cache_creation_input_tokens"] = canonical.cache_write_tokens
        if canonical.reasoning_tokens:
            usage_details["reasoning_tokens"] = canonical.reasoning_tokens
        cost = estimate_usage_cost(
            model,
            canonical,
            provider=provider,
            base_url=base_url,
            api_key="",
        )
        if cost.amount_usd is not None:
            # Langfuse cost_details keys must match usage_details keys.
            # Provide per-type breakdown so dashboard can show cost by type.
            try:
                from agent.usage_pricing import get_pricing_entry
                from decimal import Decimal
                _ONE_M = Decimal("1000000")
                entry = get_pricing_entry(model, provider=provider, base_url=base_url)
                if entry:
                    if entry.input_cost_per_million is not None and canonical.input_tokens:
                        cost_details["input"] = float(Decimal(canonical.input_tokens) * entry.input_cost_per_million / _ONE_M)
                    if entry.output_cost_per_million is not None and canonical.output_tokens:
                        cost_details["output"] = float(Decimal(canonical.output_tokens) * entry.output_cost_per_million / _ONE_M)
                    if entry.cache_read_cost_per_million is not None and canonical.cache_read_tokens:
                        cost_details["cache_read_input_tokens"] = float(Decimal(canonical.cache_read_tokens) * entry.cache_read_cost_per_million / _ONE_M)
                    if entry.cache_write_cost_per_million is not None and canonical.cache_write_tokens:
                        cost_details["cache_creation_input_tokens"] = float(Decimal(canonical.cache_write_tokens) * entry.cache_write_cost_per_million / _ONE_M)
                else:
                    cost_details["total"] = float(cost.amount_usd)
            except Exception:
                cost_details["total"] = float(cost.amount_usd)
    except Exception as exc:  # pragma: no cover - fail-open
        _debug(f"usage normalization failed: {exc}")

    return usage_details, cost_details


def _start_root_trace(task_key: str, *, task_id: str, session_id: str, platform: str, provider: str, model: str,
                      api_mode: str, messages: Any, client: Langfuse) -> TraceState:
    trace_id = client.create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")
    trace_input = _extract_last_user_message(messages)
    # Langfuse SDK v4 metadata must be dict[str, str] with values ≤200 chars.
    # Store structured context as string values; keep it compact.
    metadata = {
        "source": "hermes",
        "task_id": str(task_id)[:200] if task_id else "",
        "platform": str(platform)[:200] if platform else "",
        "provider": str(provider)[:200] if provider else "",
        "model": str(model)[:200] if model else "",
        "api_mode": str(api_mode)[:200] if api_mode else "",
    }

    # Build feature tags for per-feature analytics in the dashboard.
    # Tags help filter traces by conversation type, tool usage, etc.
    tags = ["hermes"]
    if platform:
        tags.append(f"platform:{platform}")
    if provider:
        tags.append(f"provider:{provider}")
    if api_mode:
        tags.append(f"mode:{api_mode}")

    # user_id enables cost attribution and per-user filtering.
    user_id = _get_user_id()

    # session_id must be passed in trace_context for Langfuse session grouping.
    trace_ctx: Dict[str, Any] = {"trace_id": trace_id}
    if session_id:
        trace_ctx["session_id"] = session_id

    if propagate_attributes is not None:
        try:
            with propagate_attributes(
                session_id=session_id or task_key,
                user_id=user_id or None,
                trace_name="Hermes turn",
                tags=tags,
            ):
                root_ctx = client.start_as_current_observation(
                    trace_context=trace_ctx,
                    name="Hermes turn",
                    as_type="chain",
                    input=trace_input,
                    metadata=metadata,
                    end_on_exit=False,
                )
                root_span = root_ctx.__enter__()
        except Exception:
            root_ctx = client.start_as_current_observation(
                trace_context=trace_ctx,
                name="Hermes turn",
                as_type="chain",
                input=trace_input,
                metadata=metadata,
                end_on_exit=False,
            )
            root_span = root_ctx.__enter__()
    else:
        root_ctx = client.start_as_current_observation(
            trace_context=trace_ctx,
            name="Hermes turn",
            as_type="chain",
            input=trace_input,
            metadata=metadata,
            end_on_exit=False,
        )
        root_span = root_ctx.__enter__()

    _debug(f"started trace {trace_id} for {task_key}")
    return TraceState(trace_id=trace_id, root_ctx=root_ctx, root_span=root_span)


def _start_child_observation(state: TraceState, *, client: Langfuse, name: str, as_type: str,
                             input_value: Any, metadata: Optional[dict] = None,
                             model: Optional[str] = None, model_parameters: Optional[dict] = None) -> Any:
    return state.root_span.start_observation(
        name=name,
        as_type=as_type,
        input=input_value,
        metadata=metadata or {},
        model=model,
        model_parameters=model_parameters,
    )


def _end_observation(observation: Any, *, output: Any = None, metadata: Optional[dict] = None,
                     usage_details: Optional[dict] = None, cost_details: Optional[dict] = None) -> None:
    if observation is None:
        return
    try:
        update_kwargs: Dict[str, Any] = {}
        if output is not None:
            update_kwargs["output"] = output
        if metadata:
            update_kwargs["metadata"] = metadata
        if usage_details:
            update_kwargs["usage_details"] = usage_details
        if cost_details:
            update_kwargs["cost_details"] = cost_details
        if update_kwargs:
            observation.update(**update_kwargs)
        observation.end()
    except Exception as exc:  # pragma: no cover - fail-open
        _debug(f"end observation failed: {exc}")


def _merge_trace_output(output: Any, state: TraceState) -> Any:
    if not state.turn_tool_calls:
        return output

    merged = dict(output) if isinstance(output, dict) else {"content": output}
    merged["tool_calls"] = list(state.turn_tool_calls)
    return merged


def _finish_trace(task_key: str, *, output: Any = None) -> None:
    client = _get_langfuse()
    if client is None:
        return

    with _STATE_LOCK:
        state = _TRACE_STATE.pop(task_key, None)
    if state is None:
        return

    try:
        for observation in state.generations.values():
            _end_observation(observation)
        for observation in state.tools.values():
            _end_observation(observation)
        for queue in state.pending_tools_by_name.values():
            for observation in queue:
                _end_observation(observation)
        final_output = _merge_trace_output(output, state)
        if final_output is not None and not state.output_set:
            # v4 best practice: set input/output directly on the root
            # observation rather than using deprecated set_trace_io().
            # Guard: if output was already set (e.g. by _on_post_llm_turn),
            # don't overwrite it with a stale merge result.
            state.root_span.update(output=final_output)
            state.output_set = True
        state.root_span.end()
    except Exception as exc:  # pragma: no cover - fail-open
        _debug(f"finish trace failed: {exc}")
    finally:
        try:
            client.flush()
        except Exception:
            pass


def _assistant_has_tool_calls(message: Any) -> bool:
    return bool(getattr(message, "tool_calls", None))


def _request_key(api_call_count: Any) -> str:
    return str(api_call_count or 0)


def on_pre_llm_call(*, task_id: str = "", session_id: str = "", platform: str = "", model: str = "",
                    provider: str = "", base_url: str = "", api_mode: str = "",
                    api_call_count: int = 0, messages: Any = None, turn_type: str = "user",
                    conversation_history: Any = None, user_message: Any = None, **_: Any) -> None:
    # Older Hermes branches used pre_llm_call for request-scoped tracing and
    # passed the actual API messages. Current Hermes also has a turn-scoped
    # pre_llm_call used for context injection; tracing that hook creates an
    # extra orphan/root trace before the real request trace. Only trace the
    # legacy request-shaped call here.
    if not isinstance(messages, list):
        return

    client = _get_langfuse()
    if client is None:
        return

    # messages is a list only for legacy Hermes branches that fired
    # pre_llm_call with API messages directly. Current Hermes fires
    # pre_llm_call for context injection (conversation_history/user_message,
    # no messages list) — tracing that would create orphan traces.
    task_key = _trace_key(task_id, session_id)

    with _STATE_LOCK:
        state = _TRACE_STATE.get(task_key)
        if state is None:
            state = _start_root_trace(
                task_key,
                task_id=task_id,
                session_id=session_id,
                platform=platform,
                provider=provider,
                model=model,
                api_mode=api_mode,
                messages=messages,
                client=client,
            )
            _TRACE_STATE[task_key] = state
        state.last_updated_at = time.time()


def on_pre_llm_request(
    *,
    task_id: str = "",
    session_id: str = "",
    platform: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_call_count: int = 0,
    request_messages: Any = None,
    messages: Any = None,
    turn_type: str = "user",
    message_count: int = 0,
    tool_count: int = 0,
    approx_input_tokens: int = 0,
    request_char_count: int = 0,
    max_tokens: Any = None,
    conversation_history: Any = None,
    user_message: Any = None,
    **_: Any,
) -> None:
    client = _get_langfuse()
    if client is None:
        return

    input_messages = _coerce_request_messages(
        request_messages=request_messages,
        messages=messages,
        conversation_history=conversation_history,
        user_message=user_message,
    )

    task_key = _trace_key(task_id, session_id)
    req_key = _request_key(api_call_count)

    with _STATE_LOCK:
        state = _TRACE_STATE.get(task_key)
        if state is None:
            state = _start_root_trace(
                task_key,
                task_id=task_id,
                session_id=session_id,
                platform=platform,
                provider=provider,
                model=model,
                api_mode=api_mode,
                messages=input_messages,
                client=client,
            )
            _TRACE_STATE[task_key] = state
        state.last_updated_at = time.time()
        previous = state.generations.pop(req_key, None)
        if previous is not None:
            _end_observation(previous)
        state.generations[req_key] = _start_child_observation(
            state,
            client=client,
            name=f"LLM call {api_call_count}",
            as_type="generation",
            input_value=_serialize_messages(input_messages),
            metadata={
                "provider": str(provider)[:200],
                "platform": str(platform)[:200],
                "api_mode": str(api_mode)[:200],
                "base_url": str(base_url)[:200],
            },
            model=model,
            model_parameters={"api_mode": api_mode, "provider": provider},
        )


def _on_post_api_request(*, task_id: str = "", session_id: str = "", provider: str = "",
                         base_url: str = "", api_mode: str = "", model: str = "",
                         api_call_count: int = 0, assistant_message: Any = None,
                         response: Any = None, api_duration: float = 0.0,
                         finish_reason: str = "", usage: Any = None,
                         assistant_content_chars: int = 0,
                         assistant_tool_call_count: int = 0, **_: Any) -> None:
    """Handle post_api_request — fires once per API call.

    Ends the matching generation observation and, for final text replies
    (no tool calls), finishes the root trace with the assistant content.
    """
    client = _get_langfuse()
    if client is None:
        return

    task_key = _trace_key(task_id, session_id)
    req_key = _request_key(api_call_count)

    with _STATE_LOCK:
        state = _TRACE_STATE.get(task_key)
        generation = state.generations.pop(req_key, None) if state else None
    if state is None or generation is None:
        return

    # Build output from the assistant message object (preferred)
    # or the raw response (fallback), never from char count alone.
    if assistant_message is not None:
        output = _serialize_assistant_message(assistant_message)
        _content_preview = repr(getattr(assistant_message, 'content', None))[:100]
        logger.info("Langfuse post_api_request: assistant_message branch, content=%s", _content_preview)
    elif response is not None:
        # Fallback: extract content directly from the raw response
        _choice = None
        _choices = getattr(response, "choices", None)
        if _choices and len(_choices) > 0:
            _choice = _choices[0]
        _msg = getattr(_choice, "message", None) if _choice else None
        _content = getattr(_msg, "content", None) if _msg else None
        # Normalize content (list/dict → str, same as conversation_loop)
        if _content is not None and not isinstance(_content, str):
            if isinstance(_content, dict):
                _content = _content.get("text", "") or _content.get("content", "") or str(_content)
            elif isinstance(_content, list):
                _parts = []
                for part in _content:
                    if isinstance(part, str):
                        _parts.append(part)
                    elif isinstance(part, dict) and part.get("type") == "text":
                        _parts.append(part.get("text", ""))
                    elif isinstance(part, dict) and "text" in part:
                        _parts.append(str(part["text"]))
                _content = "\n".join(_parts)
            else:
                _content = str(_content)
        _reasoning = getattr(_msg, "reasoning", None) if _msg else None
        _reasoning_content = getattr(_msg, "reasoning_content", None) if _msg else None
        if _reasoning is None and _reasoning_content is not None:
            _reasoning = _reasoning_content
        _tool_calls_raw = getattr(_msg, "tool_calls", None) if _msg else None
        output = {
            "content": _safe_value(_content) if _content else None,
            "reasoning": _safe_value(_reasoning),
            "tool_calls": _serialize_tool_calls(_tool_calls_raw) if _tool_calls_raw else [],
        }
        _debug(f"post_api_request: response fallback, content_type={type(_content).__name__ if _content else 'None'}")
        logger.info("Langfuse post_api_request: response fallback branch, content_type=%s, content=%s",
                    type(_content).__name__ if _content else 'None',
                    repr(_content)[:100] if _content else 'None')
    else:
        # Last resort — only char count available
        output = {
            "content": f"[{assistant_content_chars} chars]" if assistant_content_chars else None,
            "reasoning": None,
            "tool_calls": [],
        }
        _debug(f"post_api_request: char-count fallback (assistant_content_chars={assistant_content_chars})")
        logger.warning("Langfuse post_api_request: CHAR-COUNT FALLBACK (assistant_message=None, response=None, chars=%s) — content will show as '[N chars]'!",
                       assistant_content_chars)

    if output.get("tool_calls"):
        state.turn_tool_calls.extend(output["tool_calls"])

    # Extract usage
    if response is not None:
        usage_details, cost_details = _usage_and_cost(
            response, provider=provider, api_mode=api_mode, model=model, base_url=base_url,
        )
    elif isinstance(usage, dict) and usage:
        _input = usage.get("input_tokens", 0)
        _output = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
        _cache_read = usage.get("cache_read_tokens", 0)
        _cache_write = usage.get("cache_write_tokens", 0)
        _reasoning = usage.get("reasoning_tokens", 0)
        usage_details = {"input": _input, "output": _output}
        if _cache_read:
            usage_details["cache_read_input_tokens"] = _cache_read
        if _cache_write:
            usage_details["cache_creation_input_tokens"] = _cache_write
        if _reasoning:
            usage_details["reasoning_tokens"] = _reasoning
        cost_details = {}
        try:
            from agent.usage_pricing import CanonicalUsage, estimate_usage_cost, get_pricing_entry
            from decimal import Decimal
            _ONE_M = Decimal("1000000")
            _cu = CanonicalUsage(
                input_tokens=_input, output_tokens=_output,
                cache_read_tokens=_cache_read, cache_write_tokens=_cache_write,
                reasoning_tokens=_reasoning,
            )
            entry = get_pricing_entry(model, provider=provider, base_url=base_url)
            if entry:
                if entry.input_cost_per_million is not None and _input:
                    cost_details["input"] = float(Decimal(_input) * entry.input_cost_per_million / _ONE_M)
                if entry.output_cost_per_million is not None and _output:
                    cost_details["output"] = float(Decimal(_output) * entry.output_cost_per_million / _ONE_M)
                if entry.cache_read_cost_per_million is not None and _cache_read:
                    cost_details["cache_read_input_tokens"] = float(Decimal(_cache_read) * entry.cache_read_cost_per_million / _ONE_M)
                if entry.cache_write_cost_per_million is not None and _cache_write:
                    cost_details["cache_creation_input_tokens"] = float(Decimal(_cache_write) * entry.cache_write_cost_per_million / _ONE_M)
            else:
                _cost = estimate_usage_cost(model, _cu, provider=provider, base_url=base_url, api_key="")
                if _cost.amount_usd is not None:
                    cost_details["total"] = float(_cost.amount_usd)
        except Exception:
            pass
    else:
        usage_details, cost_details = {}, {}

    tool_count = len(output.get("tool_calls", [])) or assistant_tool_call_count
    gen_metadata: Dict[str, str] = {"tool_call_count": str(tool_count)}
    if api_duration and api_duration > 0:
        gen_metadata["api_duration_s"] = str(round(api_duration, 3))
    if finish_reason:
        gen_metadata["finish_reason"] = str(finish_reason)[:200]
    _debug(f"post_api_request: output={json.dumps(output, default=str, ensure_ascii=False)[:500]}")

    _end_observation(
        generation,
        output=output,
        usage_details=usage_details,
        cost_details=cost_details,
        metadata=gen_metadata,
    )

    has_tools = _assistant_has_tool_calls(assistant_message) if assistant_message else (assistant_tool_call_count > 0)
    has_content = bool(output.get("content"))
    if not has_tools and has_content:
        _finish_trace(task_key, output=output)


def _on_post_llm_turn(*, session_id: str = "", user_message: Any = None,
                       assistant_response: Any = None, conversation_history: Any = None,
                       model: str = "", platform: str = "", **_: Any) -> None:
    """Handle post_llm_call — fires once per turn after the tool loop ends.

    The generation observations have already been ended by post_api_request.
    This hook ensures the root trace gets finished even when the last API
    response contained tool_calls (which prevented _finish_trace in
    _on_post_api_request).  Without this, long-running agent loops (kanban
    workers, rediscover flows) accumulate open traces that never flush.
    """
    client = _get_langfuse()
    if client is None:
        return

    # post_llm_call doesn't pass task_id in all Hermes versions —
    # try session_id as the lookup key, or scan for open traces.
    if not session_id:
        return

    # Find any open trace for this session (could be keyed by task_id+session_id)
    _found = False
    matched_key = None
    with _STATE_LOCK:
        for task_key, state in list(_TRACE_STATE.items()):
            if session_id in task_key:
                matched_key = task_key
                # If the trace is still open and has no root output yet, set it
                if state.root_span and not state.output_set:
                    final_output = _safe_value(assistant_response) if isinstance(assistant_response, str) else None
                    if final_output:
                        merged = _merge_trace_output({"content": final_output}, state)
                        state.root_span.update(output=merged)
                        state.output_set = True
                        logger.info("Langfuse post_llm_turn: set root trace output for session_id=%s", session_id)
                    _found = True
                    break

    # Always finish the trace for this session — the turn is over.
    # post_api_request only finishes on final-text replies (no tool_calls),
    # but tool-heavy agent loops never hit that branch.
    if matched_key:
        _finish_trace(matched_key, output=None)
        logger.info("Langfuse post_llm_turn: finished trace for session_id=%s", session_id)

    if not _found and not matched_key:
        logger.info("Langfuse post_llm_turn: no open trace found for session_id=%s", session_id)


def on_pre_tool_call(*, tool_name: str = "", args: Any = None, task_id: str = "",
                     session_id: str = "", tool_call_id: str = "", **_: Any) -> None:
    client = _get_langfuse()
    if client is None:
        return

    task_key = _trace_key(task_id, session_id)

    with _STATE_LOCK:
        state = _TRACE_STATE.get(task_key)
        if state is None:
            return
        observation = _start_child_observation(
            state,
            client=client,
            name=f"Tool: {tool_name}",
            as_type="tool",
            input_value=_safe_value(args),
            metadata={"tool_name": str(tool_name)[:200], "tool_call_id": str(tool_call_id)[:200]},
        )
        if tool_call_id:
            state.tools[tool_call_id] = observation
        else:
            state.pending_tools_by_name.setdefault(tool_name, []).append(observation)


def on_post_tool_call(*, tool_name: str = "", args: Any = None, result: Any = None,
                      task_id: str = "", session_id: str = "", tool_call_id: str = "", **_: Any) -> None:
    task_key = _trace_key(task_id, session_id)
    observation = None

    with _STATE_LOCK:
        state = _TRACE_STATE.get(task_key)
        if state is None:
            return
        if tool_call_id:
            observation = state.tools.pop(tool_call_id, None)
        if observation is None:
            queue = state.pending_tools_by_name.get(tool_name)
            if queue:
                observation = queue.pop(0)
                if not queue:
                    state.pending_tools_by_name.pop(tool_name, None)

    if observation is None:
        return

    if isinstance(result, str):
        result_value = _maybe_parse_json_string(result)
    else:
        result_value = result
    result_value = _normalize_payload(result_value, tool_name=tool_name, args=args)
    safe_result_value = _safe_value(result_value, parse_json_strings=True)

    # Backfill so the generation's tool_call record carries the result alongside arguments.
    if tool_call_id:
        with _STATE_LOCK:
            state = _TRACE_STATE.get(task_key)
            if state is not None:
                for tool_call in reversed(state.turn_tool_calls):
                    if tool_call.get("id") == tool_call_id:
                        tool_call["output"] = safe_result_value
                        function_payload = tool_call.get("function")
                        if isinstance(function_payload, dict):
                            function_payload["output"] = safe_result_value
                        break

    _end_observation(
        observation,
        output=safe_result_value,
        metadata={"tool_name": str(tool_name)[:200], "args": str(_safe_value(args, parse_json_strings=True))[:200]},
    )


def register(ctx) -> None:
    logger.info("Langfuse plugin v%s loading (split handlers: post_api_request + post_llm_turn)", _PLUGIN_VERSION)

    # per-API-call hooks (fire once per LLM request):
    #   pre_api_request  → creates generation observation
    #   post_api_request → ends generation, extracts actual content + usage,
    #                       finishes root trace on final text reply
    ctx.register_hook("pre_api_request", on_pre_llm_request)
    ctx.register_hook("post_api_request", _on_post_api_request)

    # per-turn hooks (fire once after the tool-calling loop completes):
    #   pre_llm_call  → creates root trace if not yet started (legacy path)
    #   post_llm_call → ensures root trace output is set from final response
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_llm_call", _on_post_llm_turn)

    # per-tool-call hooks
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
