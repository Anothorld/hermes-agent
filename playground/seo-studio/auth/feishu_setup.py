"""Feishu H5 setup hints for operators (redirect URL / trusted domain checklist)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request

from auth import feishu_h5_client


def page_url_from_request(request: Request) -> str:
    """URL Feishu validates for requestAuthCode (scheme + host + port + trailing /)."""
    override = os.environ.get("SEO_STUDIO_PUBLIC_URL", "").strip().rstrip("/")
    if override:
        return override + "/"
    host = (request.headers.get("host") or "").strip()
    if not host:
        return ""
    forwarded = (request.headers.get("x-forwarded-proto") or "").strip().lower()
    scheme = forwarded if forwarded in ("http", "https") else request.url.scheme
    return f"{scheme}://{host}/"


def build_setup_hint(request: Request) -> dict[str, Any]:
    """Operator-facing checklist for Feishu open platform (error 10236)."""
    page_url = page_url_from_request(request)
    base = page_url.rstrip("/")
    app_id = feishu_h5_client.app_id() if feishu_h5_client.is_configured() else ""
    app_ok = False
    app_err = ""
    if feishu_h5_client.is_configured():
        try:
            feishu_h5_client._get_app_access_token()
            app_ok = True
        except feishu_h5_client.FeishuH5Error as exc:
            app_err = exc.msg

    return {
        "app_id": app_id,
        "app_credentials_ok": app_ok,
        "app_credentials_error": app_err,
        "page_url": page_url,
        "redirect_url": page_url,
        "h5_trusted_domain": base,
        "webapp_homepage_desktop": base,
        "webapp_homepage_mobile": base,
        "console_url": "https://open.feishu.cn/app",
        "checklist": [
            f"应用 App ID 必须为 {app_id or '(未配置)'}（凭证与基础信息页核对）",
            f"网页应用 → 桌面端主页 = {base or '(见当前访问地址)'}",
            f"网页应用 → 移动端主页 = {base or '(见当前访问地址)'}",
            f"安全设置 → 重定向 URL = {page_url or '(必须以 / 结尾)'}",
            f"安全设置 → H5 可信域名 = {base or '(与主页相同，无尾部 /)'}",
            "版本管理与发布 → 创建并发布新版本（修改 URL 后必须发布）",
            "版本详情 → 可用范围包含当前飞书用户",
            "从飞书工作台打开应用（勿用未登记的其它 IP/端口）",
        ],
        "error_10236_hint": (
            "飞书错误 10236 表示当前页面 URL 不在该应用的重定向 URL 列表中。"
            "请把 page_url 原样填入后台，http/https 与端口必须一致。"
        ),
    }
