"""DingTalk Docs & Wiki API client.

API paths sourced from alibabacloud-dingtalk SDK (doc_2_0 / wiki_2_0).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def get_access_token() -> str:
    """Get a valid DingTalk access token, refreshing if needed."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    client_id = os.environ.get("DINGTALK_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DINGTALK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise DingTalkDocsConfigError(
            "DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET env vars are required"
        )

    resp = requests.post(
        "https://api.dingtalk.com/v1.0/oauth2/accessToken",
        json={"appKey": client_id, "appSecret": client_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise DingTalkDocsAPIError(f"Failed to get access token: {data}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + data.get("expireIn", 7200)
    return token


def invalidate_token() -> None:
    """Force token refresh on next API call."""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

_BASE = "https://api.dingtalk.com"


def _headers() -> dict[str, str]:
    token = get_access_token()
    return {
        "x-acs-dingtalk-access-token": token,
        "Content-Type": "application/json",
    }


def _api_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{_BASE}{path}", headers=_headers(), params=params or {}, timeout=30)
    return _handle_response(resp)


def _api_post(path: str, body: dict | None = None, params: dict | None = None) -> dict:
    resp = requests.post(f"{_BASE}{path}", headers=_headers(), json=body or {}, params=params or {}, timeout=30)
    return _handle_response(resp)


def _handle_response(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        return {"raw": resp.text}

    if "code" in data and str(data["code"]) not in ("", "OK", "0", "success"):
        raise DingTalkDocsAPIError(
            f"DingTalk API error: code={data['code']}, message={data.get('message', '')}",
            api_code=str(data["code"]),
        )
    return data


# ---------------------------------------------------------------------------
# Doc APIs (doc_2_0)
# ---------------------------------------------------------------------------

def search_docs(keyword: str, max_results: int = 10) -> dict:
    """Search documents (doc_2_0 Search).
    Path: POST /v2.0/doc/search
    """
    return _api_post("/v2.0/doc/search", body={
        "dentryRequest": {"keyword": keyword},
        "maxResults": max_results,
    })


def get_doc_content(dentry_uuid: str, content_type: str = "text") -> dict:
    """Get document content (doc_2_0 GetDocContent).
    Path: GET /v2.0/doc/me/query/{dentry_uuid}/contents
    """
    return _api_get(
        f"/v2.0/doc/me/query/{dentry_uuid}/contents",
        params={"contentType": content_type},
    )


def query_doc_content(dentry_uuid: str) -> dict:
    """Query doc content via async job (doc_2_0 QueryDocContent).
    Path: POST /v2.0/doc/query/{dentry_uuid}/contents
    """
    return _api_post(f"/v2.0/doc/query/{dentry_uuid}/contents")


def query_item_by_url(url: str) -> dict:
    """Query item by URL (doc_2_0 QueryItemByUrl).
    Path: POST /v2.0/doc/items
    """
    return _api_post("/v2.0/doc/items", body={"url": url})


def list_recents(max_results: int = 20) -> dict:
    """List recent documents (doc_2_0 ListRecents).
    Path: POST /v2.0/doc/dentries/recentRecords/lists/query
    """
    return _api_post(
        "/v2.0/doc/dentries/recentRecords/lists/query",
        body={"maxResults": max_results},
    )


def get_my_space() -> dict:
    """Get my space info (doc_2_0 GetMySpace).
    Path: GET /v2.0/doc/me/mySpace/infos
    """
    return _api_get("/v2.0/doc/me/mySpace/infos")


# ---------------------------------------------------------------------------
# Wiki APIs (wiki_2_0)
# ---------------------------------------------------------------------------

def list_wiki_workspaces(max_results: int = 20, next_token: str = "") -> dict:
    """List wiki workspaces (wiki_2_0 ListWorkspaces).
    Path: GET /v2.0/wiki/workspaces
    """
    params: dict[str, Any] = {"maxResults": max_results}
    if next_token:
        params["nextToken"] = next_token
    return _api_get("/v2.0/wiki/workspaces", params=params)


def list_org_workspaces(max_results: int = 20, next_token: str = "") -> dict:
    """List org wiki workspaces (wiki_2_0 ListOrgWorkspaces).
    Path: GET /v2.0/wiki/org/workspaces
    """
    params: dict[str, Any] = {"maxResults": max_results}
    if next_token:
        params["nextToken"] = next_token
    return _api_get("/v2.0/wiki/org/workspaces", params=params)


def list_wiki_nodes(workspace_id: str, parent_node_id: str = "", max_results: int = 50,
                    next_token: str = "") -> dict:
    """List wiki nodes (wiki_2_0 ListNodes).
    Path: GET /v2.0/wiki/nodes
    """
    params: dict[str, Any] = {"workspaceId": workspace_id, "maxResults": max_results}
    if parent_node_id:
        params["parentNodeId"] = parent_node_id
    if next_token:
        params["nextToken"] = next_token
    return _api_get("/v2.0/wiki/nodes", params=params)


def get_wiki_node(workspace_id: str, node_id: str) -> dict:
    """Get wiki node detail (wiki_2_0 GetNode).
    Path: GET /v2.0/wiki/nodes/{node_id}
    """
    params: dict[str, Any] = {"workspaceId": workspace_id}
    return _api_get(f"/v2.0/wiki/nodes/{node_id}", params=params)


def get_node_by_url(url: str) -> dict:
    """Get wiki node by URL (wiki_2_0 GetNodeByUrl).
    Path: POST /v2.0/wiki/nodes/queryByUrl
    """
    return _api_post("/v2.0/wiki/nodes/queryByUrl", body={"url": url})


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class DingTalkDocsError(Exception):
    """Base error for DingTalk Docs plugin."""


class DingTalkDocsConfigError(DingTalkDocsError):
    """Configuration error (missing env vars, etc.)."""


class DingTalkDocsAPIError(DingTalkDocsError):
    """API error from DingTalk."""

    def __init__(self, message: str, api_code: str = "", status_code: int = 0):
        super().__init__(message)
        self.api_code = api_code
        self.status_code = status_code
