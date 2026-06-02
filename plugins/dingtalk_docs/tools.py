"""Hermes tool registration helpers for DingTalk Docs & Wiki."""

from __future__ import annotations

import json
import os
from typing import Any

from plugins.dingtalk_docs.internal.client import (
    DingTalkDocsAPIError,
    DingTalkDocsConfigError,
    get_doc_content,
    get_node_by_url,
    get_wiki_node,
    list_org_workspaces,
    list_recents,
    list_wiki_nodes,
    list_wiki_workspaces,
    query_doc_content,
    query_item_by_url,
    search_docs,
)
from tools.registry import tool_error, tool_result


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_dingtalk_docs_available() -> bool:
    """Return True when DingTalk credentials are configured."""
    return bool(
        os.environ.get("DINGTALK_CLIENT_ID", "").strip()
        and os.environ.get("DINGTALK_CLIENT_SECRET", "").strip()
    )


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

DINGTALK_DOC_GET_CONTENT_SCHEMA = {
    "name": "dingtalk_doc_get_content",
    "description": (
        "读取钉钉文档内容。支持通过文档ID或钉钉文档URL获取文档正文内容。"
        "返回纯文本格式。如果文档较大，会通过异步任务获取。"
        "Read DingTalk document content by doc ID or URL. Returns plain text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "description": "钉钉文档ID (dentryId)。与 url 二选一。",
            },
            "url": {
                "type": "string",
                "description": "钉钉文档URL。与 doc_id 二选一。支持知识库和普通文档链接。",
            },
            "content_type": {
                "type": "string",
                "enum": ["text", "html", "markdown"],
                "description": "返回内容格式。默认 text (纯文本)。",
            },
        },
    },
}

DINGTALK_DOC_SEARCH_SCHEMA = {
    "name": "dingtalk_doc_search",
    "description": (
        "搜索钉钉文档。根据关键词搜索你有权限访问的钉钉文档。"
        "Search DingTalk documents by keyword across accessible spaces."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回数量，默认10",
            },
        },
        "required": ["keyword"],
    },
}

DINGTALK_WIKI_LIST_WORKSPACES_SCHEMA = {
    "name": "dingtalk_wiki_list_workspaces",
    "description": (
        "列出钉钉知识库空间。获取你有权限访问的知识库（Wiki）空间列表。"
        "List DingTalk Wiki workspaces accessible to the authenticated app."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "最大返回数量，默认20",
            },
            "next_token": {
                "type": "string",
                "description": "翻页令牌，首次请求不传",
            },
            "org_scope": {
                "type": "boolean",
                "description": "是否列出组织级知识库（默认 false，列出个人知识库）",
            },
        },
    },
}

DINGTALK_WIKI_LIST_NODES_SCHEMA = {
    "name": "dingtalk_wiki_list_nodes",
    "description": (
        "列出知识库空间下的文档节点。获取某个知识库空间下的文档目录树。"
        "List wiki document nodes under a workspace. Returns the directory tree."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "知识库空间ID",
            },
            "parent_node_id": {
                "type": "string",
                "description": "父节点ID，不传则返回根目录节点",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回数量，默认50",
            },
            "next_token": {
                "type": "string",
                "description": "翻页令牌",
            },
        },
        "required": ["workspace_id"],
    },
}

DINGTALK_WIKI_GET_NODE_SCHEMA = {
    "name": "dingtalk_wiki_get_node",
    "description": (
        "获取知识库文档节点详情。返回节点标题、作者、更新时间等元数据。"
        "Get wiki node details: title, author, update time, etc."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "知识库空间ID",
            },
            "node_id": {
                "type": "string",
                "description": "文档节点ID",
            },
        },
        "required": ["workspace_id", "node_id"],
    },
}

DINGTALK_DOC_LIST_RECENTS_SCHEMA = {
    "name": "dingtalk_doc_list_recents",
    "description": (
        "列出最近访问的钉钉文档。"
        "List recently accessed DingTalk documents."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "max_results": {
                "type": "integer",
                "description": "最大返回数量，默认20",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_dingtalk_doc_get_content(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_doc_get_content tool call."""
    try:
        doc_id = args.get("doc_id", "")
        url = args.get("url", "")
        content_type = args.get("content_type", "text")

        if not doc_id and not url:
            return tool_error("必须提供 doc_id 或 url 参数", invalid_input=True)

        # If URL provided, try both doc and wiki URL resolution
        if url and not doc_id:
            # Try wiki node by URL first
            try:
                node_info = get_node_by_url(url)
                node_data = node_info.get("body", node_info)
                node = node_data.get("node", {})
                doc_id = node.get("dentryId", "")
                if not doc_id:
                    # Try doc item by URL
                    item_info = query_item_by_url(url)
                    item_data = item_info.get("body", item_info)
                    doc_id = item_data.get("dentryUuid", item_data.get("id", ""))
            except DingTalkDocsAPIError:
                # Fall back to doc queryItemByUrl
                try:
                    item_info = query_item_by_url(url)
                    item_data = item_info.get("body", item_info)
                    doc_id = item_data.get("dentryUuid", item_data.get("id", ""))
                except DingTalkDocsAPIError:
                    pass

            if not doc_id:
                return tool_result(json.dumps(
                    {"status": "url_resolved_failed", "url": url, "tip": "无法从URL提取文档ID，请直接提供doc_id (dentryUuid)"},
                    ensure_ascii=False, indent=2,
                ))

        # Try sync get content first
        result = get_doc_content(doc_id, content_type=content_type)
        body = result.get("body", result)

        # If content is empty, try async query
        content = body.get("content", body.get("text", ""))
        if not content:
            async_result = query_doc_content(doc_id)
            body = async_result.get("body", async_result)
            content = body.get("content", "")

        return tool_result(json.dumps(
            {
                "doc_id": doc_id,
                "content_type": content_type,
                "content": content,
                "title": body.get("title", ""),
            },
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk doc get content failed: {type(exc).__name__}: {exc}")


def _handle_dingtalk_doc_search(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_doc_search tool call."""
    try:
        keyword = args["keyword"]
        max_results = args.get("max_results", 10)
        result = search_docs(keyword, max_results=max_results)
        body = result.get("body", result)

        # Extract search results
        dentry_result = body.get("dentryResult", {})
        items = dentry_result.get("items", [])
        docs = []
        for item in items:
            docs.append({
                "doc_id": item.get("id", ""),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "updated_at": item.get("updatedAt", ""),
                "creator": item.get("creator", {}).get("name", "") if isinstance(item.get("creator"), dict) else "",
                "url": item.get("url", ""),
            })

        return tool_result(json.dumps(
            {"keyword": keyword, "total": len(docs), "documents": docs},
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk doc search failed: {type(exc).__name__}: {exc}")


def _handle_dingtalk_wiki_list_workspaces(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_wiki_list_workspaces tool call."""
    try:
        max_results = args.get("max_results", 20)
        next_token = args.get("next_token", "")
        org_scope = args.get("org_scope", False)

        if org_scope:
            result = list_org_workspaces(max_results=max_results, next_token=next_token)
        else:
            result = list_wiki_workspaces(max_results=max_results, next_token=next_token)

        body = result.get("body", result)
        workspaces_raw = body.get("workspaces", [])
        workspaces = []
        for ws in workspaces_raw:
            workspaces.append({
                "workspace_id": ws.get("id", ws.get("workspaceId", "")),
                "name": ws.get("name", ""),
                "description": ws.get("description", ""),
                "node_count": ws.get("nodeCount", 0),
                "creator": ws.get("creator", {}).get("name", "") if isinstance(ws.get("creator"), dict) else "",
            })

        return tool_result(json.dumps(
            {
                "workspaces": workspaces,
                "next_token": body.get("nextToken", ""),
                "has_more": body.get("hasMore", False),
            },
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk wiki list workspaces failed: {type(exc).__name__}: {exc}")


def _handle_dingtalk_wiki_list_nodes(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_wiki_list_nodes tool call."""
    try:
        workspace_id = args["workspace_id"]
        parent_node_id = args.get("parent_node_id", "")
        max_results = args.get("max_results", 50)
        next_token = args.get("next_token", "")

        result = list_wiki_nodes(
            workspace_id=workspace_id,
            parent_node_id=parent_node_id,
            max_results=max_results,
            next_token=next_token,
        )
        body = result.get("body", result)
        nodes_raw = body.get("nodes", [])
        nodes = []
        for n in nodes_raw:
            nodes.append({
                "node_id": n.get("id", n.get("nodeId", "")),
                "title": n.get("title", ""),
                "type": n.get("type", ""),
                "updated_at": n.get("updatedAt", ""),
                "creator": n.get("creator", {}).get("name", "") if isinstance(n.get("creator"), dict) else "",
                "has_children": n.get("hasChildren", False),
                "dentry_id": n.get("dentryId", ""),
            })

        return tool_result(json.dumps(
            {
                "workspace_id": workspace_id,
                "parent_node_id": parent_node_id,
                "nodes": nodes,
                "next_token": body.get("nextToken", ""),
                "has_more": body.get("hasMore", False),
            },
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk wiki list nodes failed: {type(exc).__name__}: {exc}")


def _handle_dingtalk_wiki_get_node(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_wiki_get_node tool call."""
    try:
        workspace_id = args["workspace_id"]
        node_id = args["node_id"]

        result = get_wiki_node(workspace_id=workspace_id, node_id=node_id)
        body = result.get("body", result)
        node = body.get("node", body)

        return tool_result(json.dumps(
            {
                "workspace_id": workspace_id,
                "node_id": node_id,
                "title": node.get("title", ""),
                "type": node.get("type", ""),
                "status": node.get("status", ""),
                "creator": node.get("creator", {}),
                "updated_at": node.get("updatedAt", ""),
                "dentry_id": node.get("dentryId", ""),
                "statistical_info": node.get("statisticalInfo", {}),
            },
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk wiki get node failed: {type(exc).__name__}: {exc}")


def _handle_dingtalk_doc_list_recents(args: dict[str, Any], **_: Any) -> str:
    """Handle dingtalk_doc_list_recents tool call."""
    try:
        max_results = args.get("max_results", 20)
        result = list_recents(max_results=max_results)
        body = result.get("body", result)

        recent_list = body.get("recentDentryList", [])
        docs = []
        for item in recent_list:
            resource = item.get("resource", {})
            docs.append({
                "doc_id": resource.get("id", ""),
                "title": resource.get("title", ""),
                "type": resource.get("type", ""),
                "updated_at": resource.get("updatedAt", ""),
                "creator": resource.get("creator", {}).get("name", "") if isinstance(resource.get("creator"), dict) else "",
                "space_id": resource.get("spaceInfo", {}).get("id", "") if isinstance(resource.get("spaceInfo"), dict) else "",
            })

        return tool_result(json.dumps(
            {"recents": docs},
            ensure_ascii=False, indent=2,
        ))

    except DingTalkDocsConfigError as exc:
        return tool_error(str(exc), auth_required=True)
    except DingTalkDocsAPIError as exc:
        return tool_error(str(exc), api_code=exc.api_code, status_code=exc.status_code)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"DingTalk doc list recents failed: {type(exc).__name__}: {exc}")
