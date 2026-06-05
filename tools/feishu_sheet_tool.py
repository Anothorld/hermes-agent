"""Feishu Sheet (Spreadsheet) Tool -- create, read, write spreadsheets via Feishu/Lark API.

Provides tools for managing Feishu electronic spreadsheets (电子表格):
- feishu_sheet_create: Create a new spreadsheet
- feishu_sheet_write: Write data to a spreadsheet range
- feishu_sheet_read: Read data from a spreadsheet range
- feishu_sheet_add_sheet: Add a new sheet tab to a spreadsheet
- feishu_sheet_list_sheets: List all sheet tabs in a spreadsheet

Uses the same get_client() pattern as feishu_doc_tool.py.
"""

import json
import logging
import threading

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Thread-local storage — reuse the client from feishu_doc_tool if available.
_local = threading.local()


def set_client(client):
    """Store a lark client for the current thread."""
    _local.client = client


def get_client():
    """Return the lark client for the current thread.

    Reuse the client from feishu_doc_tool if already set, otherwise
    build one from environment variables.
    """
    client = getattr(_local, "client", None)
    if client is not None:
        return client

    # Try to reuse from feishu_doc_tool
    try:
        from tools.feishu_doc_tool import get_client as doc_get_client
        client = doc_get_client()
        if client is not None:
            _local.client = client
            return client
    except Exception:
        pass

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


def _check_feishu():
    import importlib.util
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def _get_tenant_token():
    """Get a tenant_access_token for direct HTTP calls."""
    import os
    import urllib.request

    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        return result.get("tenant_access_token", "")
    except Exception as e:
        logger.warning("Failed to get tenant token: %s", e)
        return None


def _api_request(method, uri, body=None, token=None):
    """Make a direct HTTP request to the Feishu API."""
    import urllib.request
    import urllib.error

    if not token:
        token = _get_tenant_token()
    if not token:
        return {"code": -1, "msg": "Failed to get access token"}

    url = f"https://open.feishu.cn{uri}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return json.loads(raw)
        except Exception:
            return {"code": -1, "msg": f"HTTP {e.code}: {raw[:200]}"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ---------------------------------------------------------------------------
# feishu_sheet_create
# ---------------------------------------------------------------------------

FEISHU_SHEET_CREATE_SCHEMA = {
    "name": "feishu_sheet_create",
    "description": (
        "Create a new Feishu/Lark spreadsheet (电子表格). "
        "Returns the spreadsheet token and URL. "
        "Optionally provide a title and initial data (2D array)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The spreadsheet title.",
            },
            "folder_token": {
                "type": "string",
                "description": (
                    "The folder token where the spreadsheet will be created. "
                    "If not provided, the spreadsheet is created in the user's root folder."
                ),
            },
        },
        "required": ["title"],
    },
}


def _handle_feishu_sheet_create(args: dict, **kwargs) -> str:
    title = args.get("title", "").strip()
    if not title:
        return tool_error("title is required")

    folder_token = args.get("folder_token", "").strip()

    # Create spreadsheet via v3 API
    body = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token

    result = _api_request("POST", "/open-apis/sheets/v3/spreadsheets", body)
    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to create spreadsheet: code={code} msg={result.get('msg')}")

    spread_data = result.get("data", {}).get("spreadsheet", {})
    spread_token = spread_data.get("spreadsheet_token", "")
    url = spread_data.get("url", f"https://bytedance.larkoffice.com/sheets/{spread_token}")

    if not spread_token:
        return tool_error("Spreadsheet created but could not retrieve token")

    return tool_result(
        success=True,
        spreadsheet_token=spread_token,
        url=url,
    )


# ---------------------------------------------------------------------------
# feishu_sheet_write
# ---------------------------------------------------------------------------

FEISHU_SHEET_WRITE_SCHEMA = {
    "name": "feishu_sheet_write",
    "description": (
        "Write data to a Feishu/Lark spreadsheet. "
        "Provide the spreadsheet token, range (e.g. 'sheetId!A1:C3'), and a 2D array of values. "
        "Each sub-array is a row. Use feishu_sheet_list_sheets to find sheet IDs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_token": {
                "type": "string",
                "description": "The spreadsheet token (from the spreadsheet URL).",
            },
            "range": {
                "type": "string",
                "description": (
                    "The A1-style range to write to, e.g. 'sheetId!A1:C3' or 'sheetId!A1'. "
                    "The sheet ID can be found using feishu_sheet_list_sheets."
                ),
            },
            "values": {
                "type": "array",
                "description": (
                    "2D array of values to write. Each sub-array is a row. "
                    "E.g. [['Name', 'Score'], ['Alice', 95], ['Bob', 87]]"
                ),
                "items": {
                    "type": "array",
                    "items": {"type": ["string", "number", "boolean", "null"]},
                },
            },
        },
        "required": ["spreadsheet_token", "range", "values"],
    },
}


def _handle_feishu_sheet_write(args: dict, **kwargs) -> str:
    spreadsheet_token = args.get("spreadsheet_token", "").strip()
    range_str = args.get("range", "").strip()
    values = args.get("values", [])

    if not spreadsheet_token:
        return tool_error("spreadsheet_token is required")
    if not range_str:
        return tool_error("range is required")
    if not values:
        return tool_error("values is required")

    # Convert all values to strings for the API
    str_values = []
    for row in values:
        str_row = []
        for cell in row:
            if cell is None:
                str_row.append("")
            else:
                str_row.append(str(cell))
        str_values.append(str_row)

    result = _api_request("PUT",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values",
        {
            "valueRange": {
                "range": range_str,
                "values": str_values,
            }
        }
    )

    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to write data: code={code} msg={result.get('msg')}")

    return tool_result(
        success=True,
        spreadsheet_token=spreadsheet_token,
        range=range_str,
        rows_written=len(str_values),
    )


# ---------------------------------------------------------------------------
# feishu_sheet_read
# ---------------------------------------------------------------------------

FEISHU_SHEET_READ_SCHEMA = {
    "name": "feishu_sheet_read",
    "description": (
        "Read data from a Feishu/Lark spreadsheet. "
        "Provide the spreadsheet token and range (e.g. 'sheetId!A1:C10'). "
        "Returns the values as a 2D array."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_token": {
                "type": "string",
                "description": "The spreadsheet token (from the spreadsheet URL).",
            },
            "range": {
                "type": "string",
                "description": (
                    "The A1-style range to read, e.g. 'sheetId!A1:C10'. "
                    "The sheet ID can be found using feishu_sheet_list_sheets."
                ),
            },
        },
        "required": ["spreadsheet_token", "range"],
    },
}


def _handle_feishu_sheet_read(args: dict, **kwargs) -> str:
    spreadsheet_token = args.get("spreadsheet_token", "").strip()
    range_str = args.get("range", "").strip()

    if not spreadsheet_token:
        return tool_error("spreadsheet_token is required")
    if not range_str:
        return tool_error("range is required")

    import urllib.parse
    encoded_range = urllib.parse.quote(range_str, safe="")

    result = _api_request("GET",
        f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{encoded_range}")

    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to read data: code={code} msg={result.get('msg')}")

    vr = result.get("data", {}).get("valueRange", {})
    values = vr.get("values", [])
    returned_range = vr.get("range", range_str)

    return tool_result(
        success=True,
        range=returned_range,
        values=values,
        rows=len(values),
    )


# ---------------------------------------------------------------------------
# feishu_sheet_add_sheet
# ---------------------------------------------------------------------------

FEISHU_SHEET_ADD_SHEET_SCHEMA = {
    "name": "feishu_sheet_add_sheet",
    "description": (
        "Add a new sheet tab to an existing Feishu/Lark spreadsheet. "
        "Returns the new sheet ID and title."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_token": {
                "type": "string",
                "description": "The spreadsheet token.",
            },
            "title": {
                "type": "string",
                "description": "The new sheet tab title.",
            },
            "index": {
                "type": "integer",
                "description": "Position index for the new sheet (0-based). If not provided, appends at end.",
            },
        },
        "required": ["spreadsheet_token", "title"],
    },
}


def _handle_feishu_sheet_add_sheet(args: dict, **kwargs) -> str:
    spreadsheet_token = args.get("spreadsheet_token", "").strip()
    title = args.get("title", "").strip()

    if not spreadsheet_token:
        return tool_error("spreadsheet_token is required")
    if not title:
        return tool_error("title is required")

    body = {"title": title}
    index = args.get("index")
    if index is not None:
        body["index"] = index

    result = _api_request("POST",
        f"/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets",
        body)

    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to add sheet: code={code} msg={result.get('msg')}")

    sheet_data = result.get("data", {}).get("sheet", {})
    sheet_id = sheet_data.get("sheet_id", "")
    sheet_title = sheet_data.get("title", title)

    return tool_result(
        success=True,
        spreadsheet_token=spreadsheet_token,
        sheet_id=sheet_id,
        title=sheet_title,
    )


# ---------------------------------------------------------------------------
# feishu_sheet_list_sheets
# ---------------------------------------------------------------------------

FEISHU_SHEET_LIST_SHEETS_SCHEMA = {
    "name": "feishu_sheet_list_sheets",
    "description": (
        "List all sheet tabs in a Feishu/Lark spreadsheet. "
        "Returns sheet IDs, titles, and grid dimensions. "
        "Use this to find sheet IDs before reading/writing data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_token": {
                "type": "string",
                "description": "The spreadsheet token.",
            },
        },
        "required": ["spreadsheet_token"],
    },
}


def _handle_feishu_sheet_list_sheets(args: dict, **kwargs) -> str:
    spreadsheet_token = args.get("spreadsheet_token", "").strip()
    if not spreadsheet_token:
        return tool_error("spreadsheet_token is required")

    result = _api_request("GET",
        f"/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query")

    code = result.get("code")
    if code != 0:
        return tool_error(f"Failed to list sheets: code={code} msg={result.get('msg')}")

    sheets = result.get("data", {}).get("sheets", [])
    sheet_list = []
    for s in sheets:
        grid = s.get("grid_properties", {})
        sheet_list.append({
            "sheet_id": s.get("sheet_id", ""),
            "title": s.get("title", ""),
            "index": s.get("index", 0),
            "row_count": grid.get("row_count", 0),
            "column_count": grid.get("column_count", 0),
        })

    return tool_result(
        success=True,
        spreadsheet_token=spreadsheet_token,
        sheets=sheet_list,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_sheet_create",
    toolset="feishu_doc",
    schema=FEISHU_SHEET_CREATE_SCHEMA,
    handler=_handle_feishu_sheet_create,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Create a new Feishu/Lark spreadsheet",
    emoji="\U0001f4ca",
)

registry.register(
    name="feishu_sheet_write",
    toolset="feishu_doc",
    schema=FEISHU_SHEET_WRITE_SCHEMA,
    handler=_handle_feishu_sheet_write,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Write data to a Feishu/Lark spreadsheet",
    emoji="\u270d\ufe0f",
)

registry.register(
    name="feishu_sheet_read",
    toolset="feishu_doc",
    schema=FEISHU_SHEET_READ_SCHEMA,
    handler=_handle_feishu_sheet_read,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Read data from a Feishu/Lark spreadsheet",
    emoji="\U0001f4d0",
)

registry.register(
    name="feishu_sheet_add_sheet",
    toolset="feishu_doc",
    schema=FEISHU_SHEET_ADD_SHEET_SCHEMA,
    handler=_handle_feishu_sheet_add_sheet,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Add a new sheet tab to a Feishu/Lark spreadsheet",
    emoji="\U0001f4c1",
)

registry.register(
    name="feishu_sheet_list_sheets",
    toolset="feishu_doc",
    schema=FEISHU_SHEET_LIST_SHEETS_SCHEMA,
    handler=_handle_feishu_sheet_list_sheets,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="List sheet tabs in a Feishu/Lark spreadsheet",
    emoji="\U0001f4cb",
)
