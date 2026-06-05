"""Feishu Bitable (多维表格) Tool -- list tables, query/create/update/delete records.

Provides tools for managing Feishu Bitable (多维表格 / Multidimensional Table):
- feishu_bitable_list_tables: List data tables in a Bitable app
- feishu_bitable_list_records: Query records with optional filter/sort
- feishu_bitable_create_record: Create a new record
- feishu_bitable_update_record: Update an existing record
- feishu_bitable_delete_record: Delete a record

Uses api_request / check_feishu from tools.feishu_utils.
"""

import json
import logging
import urllib.parse

from tools.feishu_utils import api_request, check_feishu, tool_error, tool_result
from tools.registry import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# feishu_bitable_list_tables
# ---------------------------------------------------------------------------

FEISHU_BITABLE_LIST_TABLES_SCHEMA = {
    "name": "feishu_bitable_list_tables",
    "description": (
        "List all data tables in a Feishu/Lark Bitable app (多维表格). "
        "Returns table IDs, names, and other metadata. "
        "Provide the app_token from the Bitable URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "The Bitable app token (from the URL, e.g. https://xxx.feishu.cn/base/APP_TOKEN).",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of tables per page (default 20, max 100).",
                "default": 20,
            },
            "page_token": {
                "type": "string",
                "description": "Page token for paginating through results.",
            },
        },
        "required": ["app_token"],
    },
}


def _handle_feishu_bitable_list_tables(args: dict, **kwargs) -> str:
    app_token = args.get("app_token", "").strip()
    if not app_token:
        return tool_error("app_token is required")

    params = []
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={int(page_size)}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={urllib.parse.quote(page_token, safe='')}")

    query = "&".join(params)
    uri = f"/open-apis/bitable/v1/apps/{app_token}/tables"
    if query:
        uri += f"?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list tables: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    tables = data.get("items", [])
    table_list = []
    for t in tables:
        table_list.append({
            "table_id": t.get("table_id", ""),
            "name": t.get("name", ""),
        })

    return tool_result(
        success=True,
        app_token=app_token,
        tables=table_list,
        total=data.get("total", len(table_list)),
        has_more=data.get("has_more", False),
        page_token=data.get("page_token", ""),
    )


# ---------------------------------------------------------------------------
# feishu_bitable_list_records
# ---------------------------------------------------------------------------

FEISHU_BITABLE_LIST_RECORDS_SCHEMA = {
    "name": "feishu_bitable_list_records",
    "description": (
        "Query records from a Feishu/Lark Bitable table with optional filter and sort. "
        "Returns record IDs and field values. "
        "Use feishu_bitable_list_tables to find the table_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "The Bitable app token.",
            },
            "table_id": {
                "type": "string",
                "description": "The table ID within the Bitable app.",
            },
            "filter": {
                "type": "string",
                "description": (
                    "Filter condition string, e.g. 'CurrentField.[Field1]=\"value\"'. "
                    "See Feishu API docs for filter syntax."
                ),
            },
            "sort": {
                "type": "string",
                "description": (
                    'Sort condition string, e.g. \'["Field1"]\' for ascending or \'["-Field1"]\' for descending. '
                    "See Feishu API docs for sort syntax."
                ),
            },
            "page_size": {
                "type": "integer",
                "description": "Number of records per page (default 20, max 100).",
                "default": 20,
            },
            "page_token": {
                "type": "string",
                "description": "Page token for paginating through results.",
            },
        },
        "required": ["app_token", "table_id"],
    },
}


def _handle_feishu_bitable_list_records(args: dict, **kwargs) -> str:
    app_token = args.get("app_token", "").strip()
    table_id = args.get("table_id", "").strip()
    if not app_token:
        return tool_error("app_token is required")
    if not table_id:
        return tool_error("table_id is required")

    params = []
    page_size = args.get("page_size", 20)
    if page_size:
        params.append(f"page_size={int(page_size)}")
    page_token = args.get("page_token", "").strip()
    if page_token:
        params.append(f"page_token={urllib.parse.quote(page_token, safe='')}")
    filter_str = args.get("filter", "").strip()
    if filter_str:
        params.append(f"filter={urllib.parse.quote(filter_str, safe='')}")
    sort_str = args.get("sort", "").strip()
    if sort_str:
        params.append(f"sort={urllib.parse.quote(sort_str, safe='')}")

    query = "&".join(params)
    uri = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    if query:
        uri += f"?{query}"

    result = api_request("GET", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list records: code={code} msg={result.get('msg')}")

    data = result.get("data", {})
    items = data.get("items", [])
    record_list = []
    for item in items:
        record_list.append({
            "record_id": item.get("record_id", ""),
            "fields": item.get("fields", {}),
        })

    return tool_result(
        success=True,
        app_token=app_token,
        table_id=table_id,
        records=record_list,
        total=data.get("total", len(record_list)),
        has_more=data.get("has_more", False),
        page_token=data.get("page_token", ""),
    )


# ---------------------------------------------------------------------------
# feishu_bitable_create_record
# ---------------------------------------------------------------------------

FEISHU_BITABLE_CREATE_RECORD_SCHEMA = {
    "name": "feishu_bitable_create_record",
    "description": (
        "Create a new record in a Feishu/Lark Bitable table. "
        "Provide field name to value mappings in the 'fields' parameter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "The Bitable app token.",
            },
            "table_id": {
                "type": "string",
                "description": "The table ID within the Bitable app.",
            },
            "fields": {
                "type": "object",
                "description": (
                    "A mapping of field names to values, e.g. "
                    '{\"Name\": \"Alice\", \"Score\": 95, \"Active\": true}'
                ),
                "additionalProperties": True,
            },
        },
        "required": ["app_token", "table_id", "fields"],
    },
}


def _handle_feishu_bitable_create_record(args: dict, **kwargs) -> str:
    app_token = args.get("app_token", "").strip()
    table_id = args.get("table_id", "").strip()
    fields = args.get("fields")

    if not app_token:
        return tool_error("app_token is required")
    if not table_id:
        return tool_error("table_id is required")
    if fields is None:
        return tool_error("fields is required")

    uri = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    body = {"fields": fields}

    result = api_request("POST", uri, body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create record: code={code} msg={result.get('msg')}")

    record = result.get("data", {}).get("record", {})
    return tool_result(
        success=True,
        app_token=app_token,
        table_id=table_id,
        record_id=record.get("record_id", ""),
        fields=record.get("fields", fields),
    )


# ---------------------------------------------------------------------------
# feishu_bitable_update_record
# ---------------------------------------------------------------------------

FEISHU_BITABLE_UPDATE_RECORD_SCHEMA = {
    "name": "feishu_bitable_update_record",
    "description": (
        "Update an existing record in a Feishu/Lark Bitable table. "
        "Provide the record_id and the field name to value mappings to update."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "The Bitable app token.",
            },
            "table_id": {
                "type": "string",
                "description": "The table ID within the Bitable app.",
            },
            "record_id": {
                "type": "string",
                "description": "The record ID to update.",
            },
            "fields": {
                "type": "object",
                "description": (
                    "A mapping of field names to new values, e.g. "
                    '{\"Score\": 100, \"Status\": \"Completed\"}'
                ),
                "additionalProperties": True,
            },
        },
        "required": ["app_token", "table_id", "record_id", "fields"],
    },
}


def _handle_feishu_bitable_update_record(args: dict, **kwargs) -> str:
    app_token = args.get("app_token", "").strip()
    table_id = args.get("table_id", "").strip()
    record_id = args.get("record_id", "").strip()
    fields = args.get("fields")

    if not app_token:
        return tool_error("app_token is required")
    if not table_id:
        return tool_error("table_id is required")
    if not record_id:
        return tool_error("record_id is required")
    if fields is None:
        return tool_error("fields is required")

    uri = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    body = {"fields": fields}

    result = api_request("PUT", uri, body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to update record: code={code} msg={result.get('msg')}")

    record = result.get("data", {}).get("record", {})
    return tool_result(
        success=True,
        app_token=app_token,
        table_id=table_id,
        record_id=record.get("record_id", record_id),
        fields=record.get("fields", fields),
    )


# ---------------------------------------------------------------------------
# feishu_bitable_delete_record
# ---------------------------------------------------------------------------

FEISHU_BITABLE_DELETE_RECORD_SCHEMA = {
    "name": "feishu_bitable_delete_record",
    "description": (
        "Delete a record from a Feishu/Lark Bitable table. "
        "Provide the record_id to delete."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "The Bitable app token.",
            },
            "table_id": {
                "type": "string",
                "description": "The table ID within the Bitable app.",
            },
            "record_id": {
                "type": "string",
                "description": "The record ID to delete.",
            },
        },
        "required": ["app_token", "table_id", "record_id"],
    },
}


def _handle_feishu_bitable_delete_record(args: dict, **kwargs) -> str:
    app_token = args.get("app_token", "").strip()
    table_id = args.get("table_id", "").strip()
    record_id = args.get("record_id", "").strip()

    if not app_token:
        return tool_error("app_token is required")
    if not table_id:
        return tool_error("table_id is required")
    if not record_id:
        return tool_error("record_id is required")

    uri = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"

    result = api_request("DELETE", uri)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to delete record: code={code} msg={result.get('msg')}")

    return tool_result(
        success=True,
        app_token=app_token,
        table_id=table_id,
        record_id=record_id,
        deleted=True,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_bitable_list_tables",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_LIST_TABLES_SCHEMA,
    handler=_handle_feishu_bitable_list_tables,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="List data tables in a Feishu/Lark Bitable app",
    emoji="\U0001f4ca",
)

registry.register(
    name="feishu_bitable_list_records",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_LIST_RECORDS_SCHEMA,
    handler=_handle_feishu_bitable_list_records,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Query records from a Feishu/Lark Bitable table",
    emoji="\U0001f50d",
)

registry.register(
    name="feishu_bitable_create_record",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_CREATE_RECORD_SCHEMA,
    handler=_handle_feishu_bitable_create_record,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a new record in a Feishu/Lark Bitable table",
    emoji="\u2795",
)

registry.register(
    name="feishu_bitable_update_record",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_UPDATE_RECORD_SCHEMA,
    handler=_handle_feishu_bitable_update_record,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Update an existing record in a Feishu/Lark Bitable table",
    emoji="\u270f\ufe0f",
)

registry.register(
    name="feishu_bitable_delete_record",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_DELETE_RECORD_SCHEMA,
    handler=_handle_feishu_bitable_delete_record,
    check_fn=check_feishu,
    requires_env=[],
    is_async=False,
    description="Delete a record from a Feishu/Lark Bitable table",
    emoji="\U0001f5d1",
)
