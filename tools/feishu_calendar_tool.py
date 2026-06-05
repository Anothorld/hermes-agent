"""Feishu Calendar Tool -- manage calendars and events via Feishu/Lark API.

Provides tools for managing Feishu calendars (日历):
- feishu_calendar_list: List calendars
- feishu_calendar_create_event: Create a calendar event
- feishu_calendar_list_events: List events in a calendar
- feishu_calendar_freebusy: Check free/busy status

Uses shared helpers from tools.feishu_utils.
"""

import logging
from datetime import datetime, timezone

from tools.feishu_utils import api_request, check_feishu
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


def _unix_to_rfc3339(unix_ts: int) -> str:
    """Convert a Unix timestamp (seconds) to RFC3339 string with +08:00 offset."""
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    # Format as RFC3339 with +08:00 timezone offset (China Standard Time)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


# ---------------------------------------------------------------------------
# feishu_calendar_list
# ---------------------------------------------------------------------------

FEISHU_CALENDAR_LIST_SCHEMA = {
    "name": "feishu_calendar_list",
    "description": (
        "List Feishu/Lark calendars. "
        "Returns calendar IDs, summaries, and types."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "Number of calendars per page (default 20, max 50).",
            },
            "page_token": {
                "type": "string",
                "description": "Page token for paginated results.",
            },
            "sync_token": {
                "type": "string",
                "description": "Sync token for incremental sync.",
            },
        },
        "required": [],
    },
}


def _handle_feishu_calendar_list(args: dict, **kwargs) -> str:
    params = []
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={page_size}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={page_token}")
    sync_token = args.get("sync_token", "").strip()
    if sync_token:
        params.append(f"sync_token={sync_token}")

    query = "&".join(params)
    uri = "/open-apis/calendar/v4/calendars"
    if query:
        uri += f"?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list calendars: code={code} msg={result.get('msg')}")

    calendars = result.get("data", {}).get("calendar_list", [])
    cal_list = []
    for c in calendars:
        cal_list.append({
            "calendar_id": c.get("calendar_id", ""),
            "summary": c.get("summary", ""),
            "description": c.get("description", ""),
            "type": c.get("type", ""),
            "role": c.get("role", ""),
        })

    has_more = result.get("data", {}).get("has_more", False)
    next_token = result.get("data", {}).get("page_token", "")
    next_sync = result.get("data", {}).get("sync_token", "")

    return tool_result(
        success=True,
        calendars=cal_list,
        has_more=has_more,
        page_token=next_token,
        sync_token=next_sync,
    )


# ---------------------------------------------------------------------------
# feishu_calendar_create_event
# ---------------------------------------------------------------------------

FEISHU_CALENDAR_CREATE_EVENT_SCHEMA = {
    "name": "feishu_calendar_create_event",
    "description": (
        "Create an event in a Feishu/Lark calendar. "
        "Provide calendar_id, summary, start_time and end_time as Unix timestamps (seconds). "
        "Optionally add description and location."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "calendar_id": {
                "type": "string",
                "description": "The calendar ID to create the event in.",
            },
            "summary": {
                "type": "string",
                "description": "The event title/summary.",
            },
            "start_time": {
                "type": "integer",
                "description": "Event start time as Unix timestamp (seconds).",
            },
            "end_time": {
                "type": "integer",
                "description": "Event end time as Unix timestamp (seconds).",
            },
            "description": {
                "type": "string",
                "description": "Event description (optional).",
            },
            "location": {
                "type": "string",
                "description": "Event location name (optional).",
            },
        },
        "required": ["calendar_id", "summary", "start_time", "end_time"],
    },
}


def _handle_feishu_calendar_create_event(args: dict, **kwargs) -> str:
    calendar_id = args.get("calendar_id", "").strip()
    summary = args.get("summary", "").strip()
    start_time = args.get("start_time")
    end_time = args.get("end_time")

    if not calendar_id:
        return tool_error("calendar_id is required")
    if not summary:
        return tool_error("summary is required")
    if start_time is None:
        return tool_error("start_time is required")
    if end_time is None:
        return tool_error("end_time is required")

    body = {
        "summary": summary,
        "start_time": {"unix": str(int(start_time))},
        "end_time": {"unix": str(int(end_time))},
    }

    description = args.get("description", "").strip()
    if description:
        body["description"] = description

    location = args.get("location", "").strip()
    if location:
        body["location"] = {"name": location}

    result = api_request("POST", f"/open-apis/calendar/v4/calendars/{calendar_id}/events", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create event: code={code} msg={result.get('msg')}")

    event = result.get("data", {}).get("event", {})
    return tool_result(
        success=True,
        event_id=event.get("event_id", ""),
        summary=event.get("summary", summary),
        start_time=event.get("start_time", {}),
        end_time=event.get("end_time", {}),
        calendar_id=calendar_id,
    )


# ---------------------------------------------------------------------------
# feishu_calendar_list_events
# ---------------------------------------------------------------------------

FEISHU_CALENDAR_LIST_EVENTS_SCHEMA = {
    "name": "feishu_calendar_list_events",
    "description": (
        "List events in a Feishu/Lark calendar within a time range. "
        "Provide start_time and end_time as Unix timestamps (seconds). "
        "Returns event IDs, summaries, and time info."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "calendar_id": {
                "type": "string",
                "description": "The calendar ID to query events from.",
            },
            "start_time": {
                "type": "integer",
                "description": "Start of time range as Unix timestamp (seconds).",
            },
            "end_time": {
                "type": "integer",
                "description": "End of time range as Unix timestamp (seconds).",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of events per page (default 20, max 50).",
            },
        },
        "required": ["calendar_id", "start_time", "end_time"],
    },
}


def _handle_feishu_calendar_list_events(args: dict, **kwargs) -> str:
    calendar_id = args.get("calendar_id", "").strip()
    start_time = args.get("start_time")
    end_time = args.get("end_time")

    if not calendar_id:
        return tool_error("calendar_id is required")
    if start_time is None:
        return tool_error("start_time is required")
    if end_time is None:
        return tool_error("end_time is required")

    params = [
        f"start_time={int(start_time)}",
        f"end_time={int(end_time)}",
    ]
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={page_size}")

    query = "&".join(params)
    uri = f"/open-apis/calendar/v4/calendars/{calendar_id}/events?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list events: code={code} msg={result.get('msg')}")

    events = result.get("data", {}).get("items", [])
    event_list = []
    for e in events:
        event_list.append({
            "event_id": e.get("event_id", ""),
            "summary": e.get("summary", ""),
            "description": e.get("description", ""),
            "start_time": e.get("start_time", {}),
            "end_time": e.get("end_time", {}),
            "location": e.get("location", {}),
        })

    has_more = result.get("data", {}).get("has_more", False)
    next_token = result.get("data", {}).get("page_token", "")

    return tool_result(
        success=True,
        calendar_id=calendar_id,
        events=event_list,
        has_more=has_more,
        page_token=next_token,
    )


# ---------------------------------------------------------------------------
# feishu_calendar_freebusy
# ---------------------------------------------------------------------------

FEISHU_CALENDAR_FREEBUSY_SCHEMA = {
    "name": "feishu_calendar_freebusy",
    "description": (
        "Check free/busy status for a user. "
        "Provide start_time and end_time as Unix timestamps (seconds). "
        "Optionally specify user_id (defaults to the current user). "
        "Returns busy time intervals."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start_time": {
                "type": "integer",
                "description": "Start of time range as Unix timestamp (seconds).",
            },
            "end_time": {
                "type": "integer",
                "description": "End of time range as Unix timestamp (seconds).",
            },
            "user_id": {
                "type": "string",
                "description": "User ID to check. If omitted, checks the current user.",
            },
        },
        "required": ["start_time", "end_time"],
    },
}


def _handle_feishu_calendar_freebusy(args: dict, **kwargs) -> str:
    start_time = args.get("start_time")
    end_time = args.get("end_time")

    if start_time is None:
        return tool_error("start_time is required")
    if end_time is None:
        return tool_error("end_time is required")

    body = {
        "time_min": _unix_to_rfc3339(int(start_time)),
        "time_max": _unix_to_rfc3339(int(end_time)),
    }

    user_id = args.get("user_id", "").strip()
    if user_id:
        body["user_id"] = {"user_id": user_id}

    result = api_request("POST", "/open-apis/calendar/v4/freebusy/list", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to check free/busy: code={code} msg={result.get('msg')}")

    freebusy = result.get("data", {}).get("freebusy", [])
    busy_list = []
    for fb in freebusy:
        busy_list.append({
            "calendar_id": fb.get("calendar_id", ""),
            "busy": fb.get("busy", []),
        })

    return tool_result(
        success=True,
        time_min=body["time_min"],
        time_max=body["time_max"],
        freebusy=busy_list,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_calendar_list",
    toolset="feishu_calendar",
    schema=FEISHU_CALENDAR_LIST_SCHEMA,
    handler=_handle_feishu_calendar_list,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List Feishu/Lark calendars",
    emoji="\U0001f4c5",
)

registry.register(
    name="feishu_calendar_create_event",
    toolset="feishu_calendar",
    schema=FEISHU_CALENDAR_CREATE_EVENT_SCHEMA,
    handler=_handle_feishu_calendar_create_event,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create an event in a Feishu/Lark calendar",
    emoji="\U0001f4c6",
)

registry.register(
    name="feishu_calendar_list_events",
    toolset="feishu_calendar",
    schema=FEISHU_CALENDAR_LIST_EVENTS_SCHEMA,
    handler=_handle_feishu_calendar_list_events,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List events in a Feishu/Lark calendar",
    emoji="\U0001f4cb",
)

registry.register(
    name="feishu_calendar_freebusy",
    toolset="feishu_calendar",
    schema=FEISHU_CALENDAR_FREEBUSY_SCHEMA,
    handler=_handle_feishu_calendar_freebusy,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Check free/busy status in Feishu/Lark calendar",
    emoji="\U0001f552",
)
