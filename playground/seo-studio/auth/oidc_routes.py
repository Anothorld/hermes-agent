"""FastAPI router for Feishu OIDC + H5 auth (SEO Studio)."""

from __future__ import annotations

import hmac
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from auth import feishu_h5_client, feishu_setup, oidc_client, operator_session, operator_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class H5TokenBody(BaseModel):
    code: str


@router.get("/feishu/login")
def feishu_login(state: str | None = None):
    state = state or secrets.token_urlsafe(16)
    auth_url = oidc_client.build_auth_url(state=state)
    resp = RedirectResponse(auth_url, status_code=302)
    resp.set_cookie(
        "oidc_state",
        state,
        httponly=True,
        secure=operator_session.cookie_secure(),
        samesite="lax",
        max_age=600,
    )
    return resp


@router.get("/feishu/callback")
def feishu_callback(code: str, state: str, request: Request):
    cookie_state = request.cookies.get("oidc_state")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="invalid state（登录已过期，请重新登录）")
    try:
        tokens = oidc_client.exchange_code(code)
    except oidc_client.OIDCError as exc:
        raise HTTPException(status_code=400, detail=f"飞书授权失败：{exc.error}") from exc
    try:
        user = oidc_client.fetch_userinfo(tokens.get("access_token", ""))
    except oidc_client.OIDCError as exc:
        raise HTTPException(status_code=502, detail=f"获取用户信息失败：{exc.error}") from exc
    if not user.get("sub"):
        raise HTTPException(status_code=502, detail="userinfo 缺少 sub")

    op = operator_store.upsert(
        oidc_sub=user["sub"], name=user.get("name", ""), email=user.get("email", "")
    )
    if not op:
        raise HTTPException(status_code=500, detail="operator upsert failed")
    if op.get("disabled"):
        raise HTTPException(status_code=403, detail="账号已禁用，请联系管理员")

    session = operator_session.create(
        operator_id=op["id"], oidc_sub=op["oidc_sub"], name=op.get("name") or "",
    )
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(
        operator_session.COOKIE_NAME,
        session.token,
        httponly=True,
        secure=operator_session.cookie_secure(),
        samesite="lax",
        max_age=operator_session.TTL_SEC,
    )
    resp.delete_cookie("oidc_state")
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(operator_session.COOKIE_NAME)
    return resp


@router.get("/me")
def me(request: Request):
    sess = operator_session.verify(request.cookies.get(operator_session.COOKIE_NAME))
    if not sess:
        raise HTTPException(status_code=401, detail="unauthenticated")
    op = operator_store.get_by_sub(sess.get("oidc_sub", ""))
    return {
        "operator_id": sess.get("operator_id"),
        "name": sess.get("name") or (op or {}).get("name") or "",
        "email": (op or {}).get("email") or "",
        "oidc_sub": sess.get("oidc_sub"),
    }


@router.get("/feishu/setup")
def feishu_setup_page(request: Request):
    """Operator checklist when H5 login fails with error 10236."""
    hint = feishu_setup.build_setup_hint(request)
    page_url = hint.get("page_url") or "(无法检测，请用浏览器地址栏完整 URL)"
    app_id = hint.get("app_id") or "—"
    cred = "✅ App ID/Secret 有效" if hint.get("app_credentials_ok") else (
        "❌ App ID/Secret 无效：" + (hint.get("app_credentials_error") or "未配置")
    )
    rows = "".join(f"<li>{item}</li>" for item in hint.get("checklist") or [])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SEO Studio · 飞书免登配置</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;background:#f5f5f7;margin:0;padding:32px 20px;color:#1d1d1f}}
.card{{max-width:640px;margin:0 auto;background:#fff;border-radius:16px;padding:28px 24px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
h1{{font-size:22px;margin:0 0 8px}} p{{color:#6e6e73;line-height:1.55;font-size:14px}}
code,pre{{background:#f2f2f7;padding:2px 6px;border-radius:6px;font-size:13px}}
pre{{display:block;padding:12px;overflow:auto;white-space:pre-wrap;word-break:break-all}}
ol{{padding-left:20px;line-height:1.7;font-size:14px}} a{{color:#0071e3}}
.btn{{display:inline-block;margin-top:16px;padding:10px 18px;background:#0071e3;color:#fff;border-radius:10px;text-decoration:none;font-size:14px}}
</style></head><body><div class="card">
<h1>飞书 H5 免登配置</h1>
<p>{hint.get("error_10236_hint")}</p>
<p><strong>{cred}</strong></p>
<p>应用 App ID：<code>{app_id}</code></p>
<p>当前页面 URL（须原样登记）：</p>
<pre id="pageUrl">{page_url}</pre>
<p>开放平台：<a href="{hint.get("console_url")}" target="_blank" rel="noopener">{hint.get("console_url")}</a></p>
<ol>{rows}</ol>
<a class="btn" href="/">返回 Studio</a>
</div></body></html>"""
    return HTMLResponse(html)


@router.get("/feishu/setup.json")
def feishu_setup_json(request: Request):
    return feishu_setup.build_setup_hint(request)


@router.post("/feishu/h5-token")
def feishu_h5_token(body: H5TokenBody):
    try:
        tokens = feishu_h5_client.exchange_code(body.code)
    except feishu_h5_client.FeishuH5Error as exc:
        raise HTTPException(status_code=400, detail=f"飞书免登失败：{exc.msg}") from exc

    open_id = str(tokens.get("open_id") or "")
    uat = tokens.get("user_access_token") or tokens.get("access_token") or ""
    name = str(tokens.get("name") or "")
    email = str(tokens.get("email") or "")
    if (not name or not email) and uat:
        try:
            ui = feishu_h5_client.fetch_userinfo(uat)
        except feishu_h5_client.FeishuH5Error as exc:
            log.warning("h5-token userinfo failed: %s", exc.msg)
            ui = {}
        open_id = open_id or str(ui.get("open_id") or "")
        name = name or str(ui.get("name") or "")
        email = email or str(ui.get("email") or "")
    if not open_id:
        raise HTTPException(status_code=502, detail="飞书未返回 open_id（检查应用权限/scope）")

    op = operator_store.upsert(oidc_sub=open_id, name=name, email=email)
    if not op:
        raise HTTPException(status_code=500, detail="operator upsert failed")
    if op.get("disabled"):
        raise HTTPException(status_code=403, detail="账号已禁用，请联系管理员")

    session = operator_session.create(
        operator_id=op["id"], oidc_sub=op["oidc_sub"], name=op.get("name") or "",
    )
    resp = JSONResponse({
        "operator_id": op["id"],
        "name": op.get("name") or "",
        "email": op.get("email") or "",
        "oidc_sub": op["oidc_sub"],
    })
    resp.set_cookie(
        operator_session.COOKIE_NAME,
        session.token,
        httponly=True,
        secure=operator_session.cookie_secure(),
        samesite="lax",
        max_age=operator_session.TTL_SEC,
    )
    return resp
