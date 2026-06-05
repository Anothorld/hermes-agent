"""Shared utilities for Feishu/Lark API tools.

Provides common helpers used by all feishu_*_tool modules:
- get_client(): thread-local lark client (lazy-built from env vars)
- api_request(): direct HTTP calls to Feishu REST APIs
- check_feishu(): probe whether lark_oapi SDK is installed
- tool_result / tool_error shortcuts
"""

import json
import logging
import os
import threading
import urllib.error
import urllib.request

from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

_local = threading.local()


def set_client(client):
    """Store a lark client for the current thread."""
    _local.client = client


def get_client():
    """Return the lark client for the current thread.

    If no client was injected, build one from FEISHU_APP_ID /
    FEISHU_APP_SECRET environment variables and cache it.
    """
    client = getattr(_local, "client", None)
    if client is not None:
        return client

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


def check_feishu():
    """Return True if the lark_oapi SDK is importable."""
    import importlib.util
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def get_tenant_token():
    """Obtain a tenant_access_token via the Feishu internal auth endpoint."""
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        return result.get("tenant_access_token", "")
    except Exception as e:
        logger.warning("Failed to get tenant token: %s", e)
        return None


def api_request(method, uri, body=None, token=None, timeout=15):
    """Make a direct HTTP request to the Feishu Open API.

    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE).
        uri: API path, e.g. ``/open-apis/bitable/v1/apps/:app_token/tables``.
        body: JSON-serializable request body (optional).
        token: Pre-obtained tenant_access_token.  If omitted, one is fetched
               automatically via :func:`get_tenant_token`.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response dict.
    """
    if not token:
        token = get_tenant_token()
    if not token:
        return {"code": -1, "msg": "Failed to get access token"}

    url = f"https://open.feishu.cn{uri}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return json.loads(raw)
        except Exception:
            return {"code": -1, "msg": f"HTTP {e.code}: {raw[:200]}"}
    except Exception as e:
        return {"code": -1, "msg": str(e)}
