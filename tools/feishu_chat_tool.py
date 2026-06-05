"""Feishu Chat (Messaging/Group) Tool -- create groups, send messages, manage chats via Feishu/Lark API.

Provides tools for managing Feishu chats (消息/群组):
- feishu_chat_create: Create a group chat
- feishu_chat_send_message: Send a message to a chat
- feishu_chat_update: Update group information
- feishu_chat_list: List group chats

Uses shared utilities from tools.feishu_utils.
"""

import json
import logging

from tools.feishu_utils import api_request, check_feishu, tool_error, tool_result
from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# feishu_chat_create
# ---------------------------------------------------------------------------

FEISHU_CHAT_CREATE_SCHEMA = {
    "name": "feishu_chat_create",
    "description": (
        "Create a new Feishu/Lark group chat (群组). "
        "Returns the chat ID. "
        "Optionally provide a description and a list of user open_ids to add."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The group chat name.",
            },
            "description": {
                "type": "string",
                "description": "The group chat description.",
            },
            "user_id_list": {
                "type": "array",
                "description": (
                    "List of user open_ids to add to the group, e.g. ['ou_xxx', 'ou_yyy']."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["name"],
    },
}


def _handle_feishu_chat_create(args: dict, **kwargs) -> str:
    name = args.get("name", "").strip()
    if not name:
        return tool_error("name is required")

    body = {
        "name": name,
        "chat_mode": "group",
        "chat_type": "private",
    }

    description = args.get("description", "").strip()
    if description:
        body["description"] = description

    user_id_list = args.get("user_id_list", [])
    if user_id_list:
        body["user_id_list"] = user_id_list

    # user_id_type=open_id tells the API that IDs in user_id_list are open_ids
    result = api_request("POST", "/open-apis/im/v1/chats?user_id_type=open_id", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create chat: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    chat_id = data.get("chat_id", "")

    return tool_result(
        success=True,
        chat_id=chat_id,
        name=name,
    )


# ---------------------------------------------------------------------------
# feishu_chat_send_message
# ---------------------------------------------------------------------------

FEISHU_CHAT_SEND_MESSAGE_SCHEMA = {
    "name": "feishu_chat_send_message",
    "description": (
        "Send a message to a Feishu/Lark chat (发送消息). "
        "Provide the chat_id, message type (text/post/image etc.), and content as a JSON string. "
        "For text messages, content format: '{\"text\":\"hello\"}'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "The chat ID to send the message to (e.g. 'oc_xxx').",
            },
            "msg_type": {
                "type": "string",
                "description": "Message type: text, post, image, etc.",
            },
            "content": {
                "type": "string",
                "description": (
                    "Message content as a JSON string. "
                    "For text: '{\"text\":\"hello\"}'. "
                    "For post: '{\"zh_cn\":{\"title\":\"Title\",\"content\":...}}'."
                ),
            },
        },
        "required": ["chat_id", "msg_type", "content"],
    },
}


def _handle_feishu_chat_send_message(args: dict, **kwargs) -> str:
    chat_id = args.get("chat_id", "").strip()
    msg_type = args.get("msg_type", "").strip()
    content = args.get("content", "").strip()

    if not chat_id:
        return tool_error("chat_id is required")
    if not msg_type:
        return tool_error("msg_type is required")
    if not content:
        return tool_error("content is required")

    # Validate content is valid JSON
    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return tool_error(f"content must be a valid JSON string: {e}")

    body = {
        "receive_id": chat_id,
        "msg_type": msg_type,
        "content": content,
    }

    result = api_request("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to send message: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    message_id = data.get("message_id", "")

    return tool_result(
        success=True,
        message_id=message_id,
        chat_id=chat_id,
        msg_type=msg_type,
    )


# ---------------------------------------------------------------------------
# feishu_chat_update
# ---------------------------------------------------------------------------

FEISHU_CHAT_UPDATE_SCHEMA = {
    "name": "feishu_chat_update",
    "description": (
        "Update a Feishu/Lark group chat's information (更新群信息). "
        "Only the fields you provide will be updated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "The chat ID to update.",
            },
            "name": {
                "type": "string",
                "description": "New group name.",
            },
            "description": {
                "type": "string",
                "description": "New group description.",
            },
        },
        "required": ["chat_id"],
    },
}


def _handle_feishu_chat_update(args: dict, **kwargs) -> str:
    chat_id = args.get("chat_id", "").strip()
    if not chat_id:
        return tool_error("chat_id is required")

    body = {}
    name = args.get("name", "").strip()
    if name:
        body["name"] = name
    description = args.get("description", "").strip()
    if description:
        body["description"] = description

    if not body:
        return tool_error("At least one field to update must be provided")

    result = api_request("PUT", f"/open-apis/im/v1/chats/{chat_id}", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to update chat: code={code} msg={result.get('msg')}")

    return tool_result(
        success=True,
        chat_id=chat_id,
    )


# ---------------------------------------------------------------------------
# feishu_chat_list
# ---------------------------------------------------------------------------

FEISHU_CHAT_LIST_SCHEMA = {
    "name": "feishu_chat_list",
    "description": (
        "List Feishu/Lark group chats (群组列表). "
        "Returns chat IDs, names, and descriptions. "
        "Supports pagination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "Number of chats per page (default 20, max 50).",
            },
            "page_token": {
                "type": "string",
                "description": "Page token for pagination, from previous response.",
            },
        },
        "required": [],
    },
}


def _handle_feishu_chat_list(args: dict, **kwargs) -> str:
    import urllib.parse

    params = []
    page_size = args.get("page_size")
    if page_size is not None:
        params.append(f"page_size={page_size}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={urllib.parse.quote(page_token, safe='')}")

    uri = "/open-apis/im/v1/chats"
    if params:
        uri += "?" + "&".join(params)

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list chats: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    items = data.get("items", [])
    chat_list = []
    for c in items:
        chat_list.append({
            "chat_id": c.get("chat_id", ""),
            "name": c.get("name", ""),
            "description": c.get("description", ""),
            "owner_id": c.get("owner_id", ""),
        })

    return tool_result(
        success=True,
        chats=chat_list,
        total=len(chat_list),
        has_more=data.get("has_more", False),
        page_token=data.get("page_token", ""),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_chat_create",
    toolset="feishu_chat",
    schema=FEISHU_CHAT_CREATE_SCHEMA,
    handler=_handle_feishu_chat_create,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a new Feishu/Lark group chat",
    emoji="\U0001f465",
)

registry.register(
    name="feishu_chat_send_message",
    toolset="feishu_chat",
    schema=FEISHU_CHAT_SEND_MESSAGE_SCHEMA,
    handler=_handle_feishu_chat_send_message,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Send a message to a Feishu/Lark chat",
    emoji="\U0001f4ac",
)

registry.register(
    name="feishu_chat_update",
    toolset="feishu_chat",
    schema=FEISHU_CHAT_UPDATE_SCHEMA,
    handler=_handle_feishu_chat_update,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Update a Feishu/Lark group chat",
    emoji="\u270f\ufe0f",
)

registry.register(
    name="feishu_chat_list",
    toolset="feishu_chat",
    schema=FEISHU_CHAT_LIST_SCHEMA,
    handler=_handle_feishu_chat_list,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List Feishu/Lark group chats",
    emoji="\U0001f4cb",
)
