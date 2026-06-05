"""Feishu Approval Tool -- create, list, get approval instances via Feishu/Lark API.

Provides tools for managing Feishu approvals (审批):
- feishu_approval_create: Create an approval instance
- feishu_approval_list: List approval instances
- feishu_approval_get: Get approval instance details

Uses shared utilities from tools.feishu_utils.
"""

import json
import logging

from tools.feishu_utils import api_request, check_feishu, tool_error, tool_result
from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# feishu_approval_create
# ---------------------------------------------------------------------------

FEISHU_APPROVAL_CREATE_SCHEMA = {
    "name": "feishu_approval_create",
    "description": (
        "Create a Feishu/Lark approval instance (创建审批). "
        "Provide the approval_code (approval definition code), form data as a JSON string, "
        "and optionally the open_id of the initiator. "
        "The form parameter is a JSON string of key-value pairs, e.g. "
        "'[{\"key\":\"Field1\",\"value\":\"Value1\"},{\"key\":\"Field2\",\"value\":\"Value2\"}]'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "approval_code": {
                "type": "string",
                "description": "The approval definition code.",
            },
            "form": {
                "type": "string",
                "description": (
                    "Form data as a JSON string. "
                    "Format: '[{\"key\":\"xxx\",\"value\":\"xxx\"}]'."
                ),
            },
            "open_id": {
                "type": "string",
                "description": "The open_id of the user who initiates the approval.",
            },
        },
        "required": ["approval_code", "form"],
    },
}


def _handle_feishu_approval_create(args: dict, **kwargs) -> str:
    approval_code = args.get("approval_code", "").strip()
    form = args.get("form", "").strip()

    if not approval_code:
        return tool_error("approval_code is required")
    if not form:
        return tool_error("form is required")

    # Validate form is valid JSON
    try:
        json.loads(form)
    except json.JSONDecodeError as e:
        return tool_error(f"form must be a valid JSON string: {e}")

    body = {
        "approval_code": approval_code,
        "form": form,
    }

    open_id = args.get("open_id", "").strip()
    if open_id:
        body["open_id"] = open_id

    result = api_request("POST", "/open-apis/approval/v4/instances", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create approval: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    instance_id = data.get("instance_id", "")
    instance_code = data.get("instance_code", "")

    return tool_result(
        success=True,
        instance_id=instance_id,
        instance_code=instance_code,
        approval_code=approval_code,
    )


# ---------------------------------------------------------------------------
# feishu_approval_list
# ---------------------------------------------------------------------------

FEISHU_APPROVAL_LIST_SCHEMA = {
    "name": "feishu_approval_list",
    "description": (
        "List Feishu/Lark approval instances (审批列表). "
        "Requires the approval_code and a time range (Unix seconds). "
        "Returns a list of approval instance IDs and their status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "approval_code": {
                "type": "string",
                "description": "The approval definition code.",
            },
            "start_time": {
                "type": "string",
                "description": "Start time as Unix timestamp in seconds, e.g. '1700000000'.",
            },
            "end_time": {
                "type": "string",
                "description": "End time as Unix timestamp in seconds, e.g. '1700086400'.",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of results per page (default 20).",
            },
        },
        "required": ["approval_code", "start_time", "end_time"],
    },
}


def _handle_feishu_approval_list(args: dict, **kwargs) -> str:
    approval_code = args.get("approval_code", "").strip()
    start_time = args.get("start_time", "").strip()
    end_time = args.get("end_time", "").strip()

    if not approval_code:
        return tool_error("approval_code is required")
    if not start_time:
        return tool_error("start_time is required")
    if not end_time:
        return tool_error("end_time is required")

    body = {
        "approval_code": approval_code,
        "start_time": start_time,
        "end_time": end_time,
    }

    page_size = args.get("page_size")
    if page_size is not None:
        body["page_size"] = page_size

    result = api_request("POST", "/open-apis/approval/v4/instances/list", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list approvals: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    instance_list = data.get("instance_list", [])
    approvals = []
    for inst in instance_list:
        approvals.append({
            "instance_id": inst.get("instance_id", ""),
            "instance_code": inst.get("instance_code", ""),
            "status": inst.get("status", ""),
            "start_time": inst.get("start_time", ""),
            "end_time": inst.get("end_time", ""),
        })

    return tool_result(
        success=True,
        approvals=approvals,
        total=len(approvals),
        has_more=data.get("has_more", False),
        page_token=data.get("page_token", ""),
    )


# ---------------------------------------------------------------------------
# feishu_approval_get
# ---------------------------------------------------------------------------

FEISHU_APPROVAL_GET_SCHEMA = {
    "name": "feishu_approval_get",
    "description": (
        "Get details of a Feishu/Lark approval instance (审批详情). "
        "Returns the full approval instance information including status, form data, and timeline."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "instance_id": {
                "type": "string",
                "description": "The approval instance ID.",
            },
        },
        "required": ["instance_id"],
    },
}


def _handle_feishu_approval_get(args: dict, **kwargs) -> str:
    instance_id = args.get("instance_id", "").strip()
    if not instance_id:
        return tool_error("instance_id is required")

    result = api_request("GET", f"/open-apis/approval/v4/instances/{instance_id}")
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to get approval: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    instance = data.get("instance", {})

    return tool_result(
        success=True,
        instance_id=instance.get("id", instance_id),
        instance_code=instance.get("instance_code", ""),
        approval_code=instance.get("approval_code", ""),
        status=instance.get("status", ""),
        start_time=instance.get("start_time", ""),
        end_time=instance.get("end_time", ""),
        form=instance.get("form", ""),
        user_id=instance.get("user_id", ""),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_approval_create",
    toolset="feishu_approval",
    schema=FEISHU_APPROVAL_CREATE_SCHEMA,
    handler=_handle_feishu_approval_create,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a Feishu/Lark approval instance",
    emoji="\U0001f4dd",
)

registry.register(
    name="feishu_approval_list",
    toolset="feishu_approval",
    schema=FEISHU_APPROVAL_LIST_SCHEMA,
    handler=_handle_feishu_approval_list,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List Feishu/Lark approval instances",
    emoji="\U0001f4cb",
)

registry.register(
    name="feishu_approval_get",
    toolset="feishu_approval",
    schema=FEISHU_APPROVAL_GET_SCHEMA,
    handler=_handle_feishu_approval_get,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Get details of a Feishu/Lark approval instance",
    emoji="\U0001f50d",
)
