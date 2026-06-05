"""Feishu Wiki (Knowledge Base) Tool -- manage wiki spaces and nodes via Feishu/Lark API.

Provides tools for managing Feishu knowledge bases (知识库):
- feishu_wiki_list_spaces: List wiki spaces
- feishu_wiki_create_node: Create a wiki node (doc/docx/sheet/bitable)
- feishu_wiki_get_node: Get node info by obj_token
- feishu_wiki_list_nodes: List child nodes in a wiki space

Uses shared helpers from tools.feishu_utils.
"""

import logging
from datetime import datetime, timezone

from tools.feishu_utils import api_request, check_feishu
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# feishu_wiki_list_spaces
# ---------------------------------------------------------------------------

FEISHU_WIKI_LIST_SPACES_SCHEMA = {
    "name": "feishu_wiki_list_spaces",
    "description": (
        "List Feishu/Lark wiki spaces (知识空间). "
        "Returns space IDs, names, and descriptions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "page_size": {
                "type": "integer",
                "description": "Number of spaces per page (default 20, max 50).",
            },
            "page_token": {
                "type": "string",
                "description": "Page token for paginated results.",
            },
        },
        "required": [],
    },
}


def _handle_feishu_wiki_list_spaces(args: dict, **kwargs) -> str:
    params = []
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={page_size}")
    page_token = args.get("page_token", "")
    if page_token:
        params.append(f"page_token={page_token}")

    query = "&".join(params)
    uri = "/open-apis/wiki/v2/spaces"
    if query:
        uri += f"?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list wiki spaces: code={code} msg={result.get('msg')}")

    spaces = result.get("data", {}).get("spaces", [])
    space_list = []
    for s in spaces:
        space_list.append({
            "space_id": s.get("space_id", ""),
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "space_type": s.get("space_type", ""),
        })

    has_more = result.get("data", {}).get("has_more", False)
    next_token = result.get("data", {}).get("page_token", "")

    return tool_result(
        success=True,
        spaces=space_list,
        has_more=has_more,
        page_token=next_token,
    )


# ---------------------------------------------------------------------------
# feishu_wiki_create_node
# ---------------------------------------------------------------------------

FEISHU_WIKI_CREATE_NODE_SCHEMA = {
    "name": "feishu_wiki_create_node",
    "description": (
        "Create a node in a Feishu/Lark wiki space (知识库节点). "
        "Supports obj_type: doc, docx, sheet, bitable. "
        "Returns the node token and obj_token."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "space_id": {
                "type": "string",
                "description": "The wiki space ID (from feishu_wiki_list_spaces).",
            },
            "obj_type": {
                "type": "string",
                "description": "Object type: doc, docx, sheet, or bitable.",
                "enum": ["doc", "docx", "sheet", "bitable"],
            },
            "title": {
                "type": "string",
                "description": "The title for the new node.",
            },
            "parent_node_token": {
                "type": "string",
                "description": "Parent node token. If not provided, node is created at the root.",
            },
        },
        "required": ["space_id", "obj_type"],
    },
}


def _handle_feishu_wiki_create_node(args: dict, **kwargs) -> str:
    space_id = args.get("space_id", "").strip()
    obj_type = args.get("obj_type", "").strip()
    if not space_id:
        return tool_error("space_id is required")
    if not obj_type:
        return tool_error("obj_type is required")

    body = {"obj_type": obj_type}
    title = args.get("title", "").strip()
    if title:
        body["title"] = title
    parent_node_token = args.get("parent_node_token", "").strip()
    if parent_node_token:
        body["parent_node_token"] = parent_node_token

    result = api_request("POST", f"/open-apis/wiki/v2/spaces/{space_id}/nodes", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create wiki node: code={code} msg={result.get('msg')}")

    node = result.get("data", {}).get("node", {})
    return tool_result(
        success=True,
        node_token=node.get("node_token", ""),
        obj_token=node.get("obj_token", ""),
        obj_type=node.get("obj_type", obj_type),
        title=node.get("title", title),
        space_id=space_id,
    )


# ---------------------------------------------------------------------------
# feishu_wiki_get_node
# ---------------------------------------------------------------------------

FEISHU_WIKI_GET_NODE_SCHEMA = {
    "name": "feishu_wiki_get_node",
    "description": (
        "Get information about a Feishu/Lark wiki node by its obj_token. "
        "Returns node details including space_id, parent_node_token, and obj_type."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "obj_token": {
                "type": "string",
                "description": "The object token of the wiki node.",
            },
        },
        "required": ["obj_token"],
    },
}


def _handle_feishu_wiki_get_node(args: dict, **kwargs) -> str:
    obj_token = args.get("obj_token", "").strip()
    if not obj_token:
        return tool_error("obj_token is required")

    result = api_request("GET", f"/open-apis/wiki/v2/spaces/get_node?obj_token={obj_token}")
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to get wiki node: code={code} msg={result.get('msg')}")

    node = result.get("data", {}).get("node", {})
    return tool_result(
        success=True,
        node_token=node.get("node_token", ""),
        obj_token=node.get("obj_token", obj_token),
        obj_type=node.get("obj_type", ""),
        space_id=node.get("space_id", ""),
        parent_node_token=node.get("parent_node_token", ""),
        title=node.get("title", ""),
    )


# ---------------------------------------------------------------------------
# feishu_wiki_list_nodes
# ---------------------------------------------------------------------------

FEISHU_WIKI_LIST_NODES_SCHEMA = {
    "name": "feishu_wiki_list_nodes",
    "description": (
        "List child nodes in a Feishu/Lark wiki space. "
        "Optionally filter by parent_node_token to list nodes under a specific parent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "space_id": {
                "type": "string",
                "description": "The wiki space ID.",
            },
            "parent_node_token": {
                "type": "string",
                "description": "Parent node token to list children of. If omitted, lists root nodes.",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of nodes per page (default 20, max 50).",
            },
            "page_token": {
                "type": "string",
                "description": "Page token for paginated results.",
            },
        },
        "required": ["space_id"],
    },
}


def _handle_feishu_wiki_list_nodes(args: dict, **kwargs) -> str:
    space_id = args.get("space_id", "").strip()
    if not space_id:
        return tool_error("space_id is required")

    params = []
    parent_node_token = args.get("parent_node_token", "").strip()
    if parent_node_token:
        params.append(f"parent_node_token={parent_node_token}")
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={page_size}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={page_token}")

    query = "&".join(params)
    uri = f"/open-apis/wiki/v2/spaces/{space_id}/nodes"
    if query:
        uri += f"?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list wiki nodes: code={code} msg={result.get('msg')}")

    nodes = result.get("data", {}).get("nodes", [])
    node_list = []
    for n in nodes:
        node_list.append({
            "node_token": n.get("node_token", ""),
            "obj_token": n.get("obj_token", ""),
            "obj_type": n.get("obj_type", ""),
            "title": n.get("title", ""),
            "parent_node_token": n.get("parent_node_token", ""),
        })

    has_more = result.get("data", {}).get("has_more", False)
    next_token = result.get("data", {}).get("page_token", "")

    return tool_result(
        success=True,
        space_id=space_id,
        nodes=node_list,
        has_more=has_more,
        page_token=next_token,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_wiki_list_spaces",
    toolset="feishu_wiki",
    schema=FEISHU_WIKI_LIST_SPACES_SCHEMA,
    handler=_handle_feishu_wiki_list_spaces,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List Feishu/Lark wiki spaces",
    emoji="\U0001f4da",
)

registry.register(
    name="feishu_wiki_create_node",
    toolset="feishu_wiki",
    schema=FEISHU_WIKI_CREATE_NODE_SCHEMA,
    handler=_handle_feishu_wiki_create_node,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a node in a Feishu/Lark wiki space",
    emoji="\U0001f4dd",
)

registry.register(
    name="feishu_wiki_get_node",
    toolset="feishu_wiki",
    schema=FEISHU_WIKI_GET_NODE_SCHEMA,
    handler=_handle_feishu_wiki_get_node,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Get info about a Feishu/Lark wiki node",
    emoji="\U0001f50d",
)

registry.register(
    name="feishu_wiki_list_nodes",
    toolset="feishu_wiki",
    schema=FEISHU_WIKI_LIST_NODES_SCHEMA,
    handler=_handle_feishu_wiki_list_nodes,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List child nodes in a Feishu/Lark wiki space",
    emoji="\U0001f4c2",
)
