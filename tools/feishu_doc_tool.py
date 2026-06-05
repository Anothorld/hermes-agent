"""Feishu Document Tool -- read document content via Feishu/Lark API.

Provides ``feishu_doc_read`` for reading document content as plain text.
Uses the same lazy-import + BaseRequest pattern as feishu_comment.py.
"""

import json
import logging
import threading

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Thread-local storage for the lark client injected by feishu_comment handler.
_local = threading.local()


def set_client(client):
    """Store a lark client for the current thread (called by feishu_comment handler)."""
    _local.client = client


def get_client():
    """Return the lark client for the current thread.

    If no client was injected (e.g. in a DM session rather than a comment
    event), attempt to build one from environment variables FEISHU_APP_ID
    and FEISHU_APP_SECRET.  The created client is cached on the thread-local
    so subsequent calls reuse it.
    """
    client = getattr(_local, "client", None)
    if client is not None:
        return client

    # Lazy-build from env vars
    import os
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None

    try:
        import lark_oapi as lark
        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        _local.client = client
        return client
    except Exception as e:
        logger.warning("Failed to build Feishu client from env vars: %s", e)
        return None


# ---------------------------------------------------------------------------
# feishu_doc_read
# ---------------------------------------------------------------------------

_RAW_CONTENT_URI = "/open-apis/docx/v1/documents/:document_id/raw_content"

FEISHU_DOC_READ_SCHEMA = {
    "name": "feishu_doc_read",
    "description": (
        "Read the full content of a Feishu/Lark document as plain text. "
        "Useful when you need more context beyond the quoted text in a comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL or comment context).",
            },
        },
        "required": ["doc_token"],
    },
}


def _check_feishu():
    # Use ``importlib.util.find_spec`` — it checks whether ``lark_oapi``
    # is importable without actually executing its ``__init__``.
    # Executing the real import here costs ~5 seconds (the SDK eagerly
    # loads websockets, dispatcher, every api/v2 model) and this probe
    # fires at every ``hermes`` startup during tool-availability
    # evaluation.  Correctness is preserved because the actual tool
    # handler still does the real import when invoked.
    import importlib.util
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def _handle_feishu_doc_read(args: dict, **kwargs) -> str:
    doc_token = args.get("doc_token", "").strip()
    if not doc_token:
        return tool_error("doc_token is required")

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu comment context)")

    try:
        from lark_oapi import AccessTokenType
        from lark_oapi.core.enum import HttpMethod
        from lark_oapi.core.model.base_request import BaseRequest
    except ImportError:
        return tool_error("lark_oapi not installed")

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri(_RAW_CONTENT_URI)
        .token_types({AccessTokenType.TENANT})
        .paths({"document_id": doc_token})
        .build()
    )

    # Tool handlers run synchronously in a worker thread (no running event
    # loop), so call the blocking lark client directly.
    response = client.request(request)

    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to read document: code={code} msg={msg}")

    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            body = json.loads(raw.content)
            content = body.get("data", {}).get("content", "")
            return tool_result(success=True, content=content)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: try response.data
    data = getattr(response, "data", None)
    if data:
        if isinstance(data, dict):
            content = data.get("content", "")
        else:
            content = getattr(data, "content", str(data))
        return tool_result(success=True, content=content)

    return tool_error("No content returned from document API")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_doc_read",
    toolset="feishu_doc",
    schema=FEISHU_DOC_READ_SCHEMA,
    handler=_handle_feishu_doc_read,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Read Feishu document content",
    emoji="\U0001f4c4",
)


# ---------------------------------------------------------------------------
# feishu_doc_create
# ---------------------------------------------------------------------------

_CREATE_DOC_URI = "/open-apis/docx/v1/documents"

FEISHU_DOC_CREATE_SCHEMA = {
    "name": "feishu_doc_create",
    "description": (
        "Create a new Feishu/Lark document. Returns the document ID and URL. "
        "Optionally provide a title and initial content (plain text lines)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The document title.",
            },
            "content": {
                "type": "string",
                "description": (
                    "Initial content for the document. Each line becomes a paragraph. "
                    "Lines starting with '# ' become headings, '## ' become heading2, "
                    "'### ' become heading3. Leave empty to create a blank document."
                ),
            },
            "folder_token": {
                "type": "string",
                "description": (
                    "The folder token where the document will be created. "
                    "If not provided, the document is created in the user's root folder."
                ),
            },
        },
        "required": ["title"],
    },
}


def _build_block_elements(text: str):
    """Convert a single line of text into a block element for the Feishu API."""
    element = {
        "text_run": {
            "content": text,
        }
    }
    return element


def _content_line_to_block(line: str) -> dict:
    """Convert a content line to a Feishu document block (paragraph or heading).

    Feishu docx v1 API only supports creating:
      - block_type 2: text (paragraph)
      - block_type 3-11: heading1-heading9
    Other block types (bullet, ordered, code, quote, etc.) are not
    supported via the create-children API, so they are rendered as
    paragraphs with prefix markers.
    """
    stripped = line.rstrip()

    # Headings
    if stripped.startswith("### "):
        return {
            "block_type": 5,  # heading3
            "heading3": {
                "elements": [_build_block_elements(stripped[4:])],
                "style": {}
            },
        }
    elif stripped.startswith("## "):
        return {
            "block_type": 4,  # heading2
            "heading2": {
                "elements": [_build_block_elements(stripped[3:])],
                "style": {}
            },
        }
    elif stripped.startswith("# "):
        return {
            "block_type": 3,  # heading1
            "heading1": {
                "elements": [_build_block_elements(stripped[2:])],
                "style": {}
            },
        }

    # Bullet list — rendered as paragraph with bullet prefix
    # (Feishu API does not support creating block_type=16 bullet blocks)
    if stripped.startswith("- ") or stripped.startswith("* "):
        return {
            "block_type": 2,  # text
            "text": {
                "elements": [_build_block_elements(f"• {stripped[2:]}")],
                "style": {}
            },
        }

    # Ordered list — rendered as paragraph with number prefix
    # (Feishu API does not support creating block_type=17 ordered blocks)
    import re
    if re.match(r"^\d+\.\s", stripped):
        return {
            "block_type": 2,  # text
            "text": {
                "elements": [_build_block_elements(stripped)],
                "style": {}
            },
        }

    # Empty line
    if not stripped:
        return {
            "block_type": 2,  # text
            "text": {
                "elements": [_build_block_elements("")],
                "style": {}
            },
        }

    # Default: paragraph
    return {
        "block_type": 2,  # text
        "text": {
            "elements": [_build_block_elements(stripped)],
            "style": {}
        },
    }


def _handle_feishu_doc_create(args: dict, **kwargs) -> str:
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu context)")

    title = args.get("title", "").strip()
    if not title:
        return tool_error("title is required")

    content = args.get("content", "") or ""
    folder_token = args.get("folder_token", "").strip()

    # Step 1: Create the document
    doc_body = {
        "title": title,
    }
    if folder_token:
        doc_body["folder_token"] = folder_token

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.POST)
        .uri(_CREATE_DOC_URI)
        .token_types({AccessTokenType.TENANT})
        .body(doc_body)
        .build()
    )

    response = client.request(request)
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to create document: code={code} msg={msg}")

    # Parse response
    doc_data = {}
    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            body_json = json.loads(raw.content)
            doc_data = body_json.get("data", {}).get("document", {})
        except (json.JSONDecodeError, AttributeError):
            pass
    if not doc_data:
        resp_data = getattr(response, "data", None)
        if isinstance(resp_data, dict):
            doc_data = resp_data.get("document", resp_data)
        elif resp_data and hasattr(resp_data, "__dict__"):
            doc_data = vars(resp_data)

    doc_id = doc_data.get("document_id", "") if isinstance(doc_data, dict) else ""
    if not doc_id:
        return tool_error("Document created but could not retrieve document ID")

    # Step 2: Add content if provided
    if content:
        lines = content.split("\n")
        children = []
        for line in lines:
            block = _content_line_to_block(line)
            children.append(block)

        _ADD_BLOCK_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
        content_body = {
            "children": children,
        }

        content_request = (
            BaseRequest.builder()
            .http_method(HttpMethod.POST)
            .uri(_ADD_BLOCK_URI)
            .token_types({AccessTokenType.TENANT})
            .paths({"document_id": doc_id, "block_id": doc_id})
            .body(content_body)
            .build()
        )

        content_response = client.request(content_request)
        content_code = getattr(content_response, "code", None)
        if content_code != 0:
            content_msg = getattr(content_response, "msg", "unknown error")
            # Document was created but content failed
            return tool_result(
                success=True,
                document_id=doc_id,
                url=f"https://bytedance.larkoffice.com/docx/{doc_id}",
                warning=f"Document created but content insertion failed: code={content_code} msg={content_msg}",
            )

    return tool_result(
        success=True,
        document_id=doc_id,
        url=f"https://bytedance.larkoffice.com/docx/{doc_id}",
    )


# ---------------------------------------------------------------------------
# feishu_doc_write
# ---------------------------------------------------------------------------

_ADD_BLOCK_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"

FEISHU_DOC_WRITE_SCHEMA = {
    "name": "feishu_doc_write",
    "description": (
        "Append content blocks to an existing Feishu/Lark document. "
        "Each line becomes a paragraph. Lines starting with '# ' become headings, "
        "'## ' become heading2, '### ' become heading3, '- ' or '* ' become bullet items, "
        "'1. ' become ordered list items."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL).",
            },
            "content": {
                "type": "string",
                "description": (
                    "The content to append. Each line becomes a block. "
                    "Supports markdown-like formatting: # heading1, ## heading2, "
                    "### heading3, - bullet, 1. ordered."
                ),
            },
        },
        "required": ["doc_token", "content"],
    },
}


def _handle_feishu_doc_write(args: dict, **kwargs) -> str:
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu context)")

    doc_token = args.get("doc_token", "").strip()
    content = args.get("content", "")
    if not doc_token:
        return tool_error("doc_token is required")
    if not content:
        return tool_error("content is required")

    lines = content.split("\n")
    children = []
    for line in lines:
        block = _content_line_to_block(line)
        children.append(block)

    body = {
        "children": children,
    }

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.POST)
        .uri(_ADD_BLOCK_URI)
        .token_types({AccessTokenType.TENANT})
        .paths({"document_id": doc_token, "block_id": doc_token})
        .body(body)
        .build()
    )

    response = client.request(request)
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to write content: code={code} msg={msg}")

    return tool_result(success=True, document_id=doc_token, blocks_added=len(children))


# ---------------------------------------------------------------------------
# feishu_doc_update
# ---------------------------------------------------------------------------

_UPDATE_BLOCK_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"

FEISHU_DOC_UPDATE_SCHEMA = {
    "name": "feishu_doc_update",
    "description": (
        "Update the text content of an existing block in a Feishu/Lark document. "
        "First use feishu_doc_read or feishu_doc_list_blocks to find the block_id, "
        "then update it with new text content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL).",
            },
            "block_id": {
                "type": "string",
                "description": "The block ID to update.",
            },
            "text": {
                "type": "string",
                "description": "The new text content for the block.",
            },
        },
        "required": ["doc_token", "block_id", "text"],
    },
}


def _handle_feishu_doc_update(args: dict, **kwargs) -> str:
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu context)")

    doc_token = args.get("doc_token", "").strip()
    block_id = args.get("block_id", "").strip()
    text = args.get("text", "")
    if not doc_token or not block_id:
        return tool_error("doc_token and block_id are required")

    body = {
        "update_text_elements": {
            "elements": [
                {
                    "text_run": {
                        "content": text,
                    }
                }
            ],
            "force_send": True,
        }
    }

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.PATCH)
        .uri(_UPDATE_BLOCK_URI)
        .token_types({AccessTokenType.TENANT})
        .paths({"document_id": doc_token, "block_id": block_id})
        .body(body)
        .build()
    )

    response = client.request(request)
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to update block: code={code} msg={msg}")

    return tool_result(success=True, document_id=doc_token, block_id=block_id)


# ---------------------------------------------------------------------------
# feishu_doc_list_blocks
# ---------------------------------------------------------------------------

_LIST_BLOCKS_URI = "/open-apis/docx/v1/documents/:document_id/blocks"

FEISHU_DOC_LIST_BLOCKS_SCHEMA = {
    "name": "feishu_doc_list_blocks",
    "description": (
        "List all blocks in a Feishu/Lark document with their IDs and types. "
        "Use this to find block_id before updating or deleting specific blocks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL).",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of blocks per page (max 500, default 500).",
                "default": 500,
            },
            "page_token": {
                "type": "string",
                "description": "Pagination token for next page.",
            },
        },
        "required": ["doc_token"],
    },
}


def _handle_feishu_doc_list_blocks(args: dict, **kwargs) -> str:
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu context)")

    doc_token = args.get("doc_token", "").strip()
    if not doc_token:
        return tool_error("doc_token is required")

    page_size = args.get("page_size", 500)
    page_token = args.get("page_token", "")

    queries = [("page_size", str(page_size))]
    if page_token:
        queries.append(("page_token", page_token))

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri(_LIST_BLOCKS_URI)
        .token_types({AccessTokenType.TENANT})
        .paths({"document_id": doc_token})
        .queries(queries)
        .build()
    )

    response = client.request(request)
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to list blocks: code={code} msg={msg}")

    data = {}
    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            body_json = json.loads(raw.content)
            data = body_json.get("data", {})
        except (json.JSONDecodeError, AttributeError):
            pass
    if not data:
        resp_data = getattr(response, "data", None)
        if isinstance(resp_data, dict):
            data = resp_data
        elif resp_data and hasattr(resp_data, "__dict__"):
            data = vars(resp_data)

    return tool_result(data)


# ---------------------------------------------------------------------------
# Registration for new tools
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_doc_create",
    toolset="feishu_doc",
    schema=FEISHU_DOC_CREATE_SCHEMA,
    handler=_handle_feishu_doc_create,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a new Feishu/Lark document",
    emoji="\U0001f4dd",
)

registry.register(
    name="feishu_doc_write",
    toolset="feishu_doc",
    schema=FEISHU_DOC_WRITE_SCHEMA,
    handler=_handle_feishu_doc_write,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Append content to a Feishu/Lark document",
    emoji="\u270d\ufe0f",
)

registry.register(
    name="feishu_doc_update",
    toolset="feishu_doc",
    schema=FEISHU_DOC_UPDATE_SCHEMA,
    handler=_handle_feishu_doc_update,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Update a block in a Feishu/Lark document",
    emoji="\u270f\ufe0f",
)

registry.register(
    name="feishu_doc_list_blocks",
    toolset="feishu_doc",
    schema=FEISHU_DOC_LIST_BLOCKS_SCHEMA,
    handler=_handle_feishu_doc_list_blocks,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="List blocks in a Feishu/Lark document",
    emoji="\U0001f4cb",
)
