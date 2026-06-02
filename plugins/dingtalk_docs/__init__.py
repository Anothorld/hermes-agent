"""DingTalk Docs & Wiki plugin for Hermes Agent."""

from __future__ import annotations

from plugins.dingtalk_docs.tools import (
    DINGTALK_DOC_GET_CONTENT_SCHEMA,
    DINGTALK_DOC_SEARCH_SCHEMA,
    DINGTALK_WIKI_LIST_WORKSPACES_SCHEMA,
    DINGTALK_WIKI_LIST_NODES_SCHEMA,
    DINGTALK_WIKI_GET_NODE_SCHEMA,
    DINGTALK_DOC_LIST_RECENTS_SCHEMA,
    _check_dingtalk_docs_available,
    _handle_dingtalk_doc_get_content,
    _handle_dingtalk_doc_search,
    _handle_dingtalk_wiki_list_workspaces,
    _handle_dingtalk_wiki_list_nodes,
    _handle_dingtalk_wiki_get_node,
    _handle_dingtalk_doc_list_recents,
)

__all__ = ["register"]


def register(ctx) -> None:
    """Register DingTalk Docs & Wiki tools."""
    ctx.register_tool(
        name="dingtalk_doc_get_content",
        toolset="dingtalk_docs",
        schema=DINGTALK_DOC_GET_CONTENT_SCHEMA,
        handler=_handle_dingtalk_doc_get_content,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
    ctx.register_tool(
        name="dingtalk_doc_search",
        toolset="dingtalk_docs",
        schema=DINGTALK_DOC_SEARCH_SCHEMA,
        handler=_handle_dingtalk_doc_search,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
    ctx.register_tool(
        name="dingtalk_wiki_list_workspaces",
        toolset="dingtalk_docs",
        schema=DINGTALK_WIKI_LIST_WORKSPACES_SCHEMA,
        handler=_handle_dingtalk_wiki_list_workspaces,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
    ctx.register_tool(
        name="dingtalk_wiki_list_nodes",
        toolset="dingtalk_docs",
        schema=DINGTALK_WIKI_LIST_NODES_SCHEMA,
        handler=_handle_dingtalk_wiki_list_nodes,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
    ctx.register_tool(
        name="dingtalk_wiki_get_node",
        toolset="dingtalk_docs",
        schema=DINGTALK_WIKI_GET_NODE_SCHEMA,
        handler=_handle_dingtalk_wiki_get_node,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
    ctx.register_tool(
        name="dingtalk_doc_list_recents",
        toolset="dingtalk_docs",
        schema=DINGTALK_DOC_LIST_RECENTS_SCHEMA,
        handler=_handle_dingtalk_doc_list_recents,
        check_fn=_check_dingtalk_docs_available,
        emoji="D",
    )
