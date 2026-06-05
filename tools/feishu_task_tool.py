"""Feishu Task Tool -- create, list, update, complete tasks via Feishu/Lark API.

Provides tools for managing Feishu tasks (任务):
- feishu_task_create: Create a new task
- feishu_task_list: List tasks
- feishu_task_update: Update a task
- feishu_task_complete: Complete a task

Uses shared utilities from tools.feishu_utils.
"""

import json
import logging

from tools.feishu_utils import api_request, check_feishu, tool_error, tool_result
from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# feishu_task_create
# ---------------------------------------------------------------------------

FEISHU_TASK_CREATE_SCHEMA = {
    "name": "feishu_task_create",
    "description": (
        "Create a new Feishu/Lark task (任务). "
        "Returns the task ID and summary. "
        "Optionally provide a description, due date (Unix milliseconds), and assignee (open_id)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The task title/summary.",
            },
            "description": {
                "type": "string",
                "description": "Detailed description of the task.",
            },
            "due_date": {
                "type": "string",
                "description": (
                    "Due date as Unix timestamp in milliseconds, e.g. '1700000000000'."
                ),
            },
            "assignee": {
                "type": "string",
                "description": "The open_id of the user to assign the task to.",
            },
        },
        "required": ["summary"],
    },
}


def _handle_feishu_task_create(args: dict, **kwargs) -> str:
    summary = args.get("summary", "").strip()
    if not summary:
        return tool_error("summary is required")

    body = {"summary": summary}

    description = args.get("description", "").strip()
    if description:
        body["description"] = description

    due_date = args.get("due_date", "").strip()
    if due_date:
        body["due_date"] = due_date

    assignee = args.get("assignee", "").strip()
    if assignee:
        body["assignee"] = {"open_id": assignee}

    result = api_request("POST", "/open-apis/task/v2/tasks", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create task: code={code} msg={result.get('msg')}")

    task = result.get("data", {}).get("task", {})
    task_id = task.get("task_id", "")

    return tool_result(
        success=True,
        task_id=task_id,
        summary=task.get("summary", summary),
    )


# ---------------------------------------------------------------------------
# feishu_task_list
# ---------------------------------------------------------------------------

FEISHU_TASK_LIST_SCHEMA = {
    "name": "feishu_task_list",
    "description": (
        "List Feishu/Lark tasks (任务). "
        "Returns a list of tasks with their IDs, summaries, and status. "
        "Supports pagination and time-range filtering."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "Number of tasks per page (default 20, max 100).",
            },
            "page_token": {
                "type": "string",
                "description": "Page token for pagination, from previous response.",
            },
            "start_time": {
                "type": "string",
                "description": "Filter start time as Unix timestamp in milliseconds.",
            },
            "end_time": {
                "type": "string",
                "description": "Filter end time as Unix timestamp in milliseconds.",
            },
        },
        "required": [],
    },
}


def _handle_feishu_task_list(args: dict, **kwargs) -> str:
    import urllib.parse

    params = []
    page_size = args.get("page_size")
    if page_size is not None:
        params.append(f"page_size={page_size}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={urllib.parse.quote(page_token, safe='')}")
    start_time = args.get("start_time", "").strip()
    if start_time:
        params.append(f"start_time={start_time}")
    end_time = args.get("end_time", "").strip()
    if end_time:
        params.append(f"end_time={end_time}")

    uri = "/open-apis/task/v2/tasks"
    if params:
        uri += "?" + "&".join(params)

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list tasks: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    items = data.get("items", [])
    task_list = []
    for t in items:
        task_list.append({
            "task_id": t.get("task_id", ""),
            "summary": t.get("summary", ""),
            "status": t.get("status", ""),
            "due_date": t.get("due_date", ""),
        })

    return tool_result(
        success=True,
        tasks=task_list,
        total=len(task_list),
        has_more=data.get("has_more", False),
        page_token=data.get("page_token", ""),
    )


# ---------------------------------------------------------------------------
# feishu_task_update
# ---------------------------------------------------------------------------

FEISHU_TASK_UPDATE_SCHEMA = {
    "name": "feishu_task_update",
    "description": (
        "Update a Feishu/Lark task (任务). "
        "Only the fields you provide will be updated. "
        "Provide the task_id and at least one field to change."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to update.",
            },
            "summary": {
                "type": "string",
                "description": "New task title/summary.",
            },
            "description": {
                "type": "string",
                "description": "New task description.",
            },
            "due_date": {
                "type": "string",
                "description": "New due date as Unix timestamp in milliseconds, e.g. '1700000000000'.",
            },
        },
        "required": ["task_id"],
    },
}


def _handle_feishu_task_update(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return tool_error("task_id is required")

    body = {}
    summary = args.get("summary", "").strip()
    if summary:
        body["summary"] = summary
    description = args.get("description", "").strip()
    if description:
        body["description"] = description
    due_date = args.get("due_date", "").strip()
    if due_date:
        body["due_date"] = due_date

    if not body:
        return tool_error("At least one field to update must be provided")

    result = api_request("PATCH", f"/open-apis/task/v2/tasks/{task_id}", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to update task: code={code} msg={result.get('msg')}")

    task = result.get("data", {}).get("task", {})
    return tool_result(
        success=True,
        task_id=task_id,
        summary=task.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# feishu_task_complete
# ---------------------------------------------------------------------------

FEISHU_TASK_COMPLETE_SCHEMA = {
    "name": "feishu_task_complete",
    "description": (
        "Complete a Feishu/Lark task (完成任务). "
        "Marks the task as done."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The ID of the task to complete.",
            },
        },
        "required": ["task_id"],
    },
}


def _handle_feishu_task_complete(args: dict, **kwargs) -> str:
    task_id = args.get("task_id", "").strip()
    if not task_id:
        return tool_error("task_id is required")

    result = api_request("POST", f"/open-apis/task/v2/tasks/{task_id}/complete", {})
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to complete task: code={code} msg={result.get('msg')}")

    return tool_result(
        success=True,
        task_id=task_id,
        status="completed",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_task_create",
    toolset="feishu_task",
    schema=FEISHU_TASK_CREATE_SCHEMA,
    handler=_handle_feishu_task_create,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a new Feishu/Lark task",
    emoji="\u2705",
)

registry.register(
    name="feishu_task_list",
    toolset="feishu_task",
    schema=FEISHU_TASK_LIST_SCHEMA,
    handler=_handle_feishu_task_list,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List Feishu/Lark tasks",
    emoji="\U0001f4cb",
)

registry.register(
    name="feishu_task_update",
    toolset="feishu_task",
    schema=FEISHU_TASK_UPDATE_SCHEMA,
    handler=_handle_feishu_task_update,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Update a Feishu/Lark task",
    emoji="\u270f\ufe0f",
)

registry.register(
    name="feishu_task_complete",
    toolset="feishu_task",
    schema=FEISHU_TASK_COMPLETE_SCHEMA,
    handler=_handle_feishu_task_complete,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Complete a Feishu/Lark task",
    emoji="\U0001f3c1",
)
