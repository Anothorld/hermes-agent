"""Direct page-level CDP for tab-pool sessions.

Concurrent ``agent-browser`` subprocesses race on a shared Chrome instance even
with page-level ``--cdp`` (2026-06-23). Tab-pool hot paths use this module to
navigate, snapshot, eval, click, and scroll via each task's page websocket — no
agent-browser required for isolation or readability.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import textwrap
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import websockets
except ImportError:  # pragma: no cover - hermes-agent depends on websockets
    websockets = None  # type: ignore[assignment]

_SNAPSHOT_JS = textwrap.dedent(
    """
    (() => {
      const compact = __COMPACT__;
      const maxInteractive = compact ? 120 : 240;
      const bodyLimit = compact ? 5000 : 14000;
      const interactive = [];
      const selectors = [
        'a[href]', 'button', '[role="button"]', '[role="link"]',
        'input', 'textarea', '[tabindex]:not([tabindex="-1"])'
      ].join(',');
      const seen = new Set();
      for (const el of document.querySelectorAll(selectors)) {
        if (interactive.length >= maxInteractive) break;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) continue;
        const text = (el.innerText || el.getAttribute('aria-label') ||
          el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
        const href = el.getAttribute('href') || '';
        const key = `${el.tagName}|${text}|${href}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const ref = `@e${interactive.length + 1}`;
        el.setAttribute('data-hermes-ref', ref);
        interactive.push({ ref, tag: el.tagName.toLowerCase(), text, href });
      }
      const lines = interactive.map((item) => {
        const hrefPart = item.href ? ` ${item.href}` : '';
        return `[${item.ref}] ${item.tag}${hrefPart} "${item.text}"`;
      });
      const body = ((document.body && document.body.innerText) || '')
        .replace(/\\s+\\n/g, '\\n').trim().slice(0, bodyLimit);
      const snapshot = lines.join('\\n') + (body ? `\\n\\n${body}` : '');
      const refs = {};
      for (const item of interactive) {
        refs[item.ref] = { tag: item.tag, text: item.text, href: item.href };
      }
      return {
        snapshot,
        refs,
        url: location.href,
        title: document.title,
        text_len: body.length,
      };
    })()
    """
).strip()


def _run_async(coro):
    """Run async code from sync browser-tool handlers."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _send_and_wait(
    ws: Any,
    msg_id: int,
    method: str,
    params: Optional[Dict[str, Any]],
    timeout: float,
) -> Dict[str, Any]:
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for CDP response to {method}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("id") == msg_id:
            if "error" in msg:
                raise RuntimeError(f"CDP {method} failed: {msg['error']}")
            return msg.get("result") or {}


async def _enable_page_runtime(ws: Any, msg_id: int, timeout: float) -> int:
    await _send_and_wait(ws, msg_id, "Page.enable", {}, timeout=min(5.0, timeout))
    await _send_and_wait(ws, msg_id + 1, "Runtime.enable", {}, timeout=min(5.0, timeout))
    return msg_id + 2


async def _evaluate_value(
    ws: Any,
    msg_id: int,
    expression: str,
    timeout: float,
) -> tuple[int, Any]:
    result = await _send_and_wait(
        ws,
        msg_id,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        timeout=timeout,
    )
    details = (result.get("result") or {})
    if details.get("exceptionDetails"):
        raise RuntimeError(str(details["exceptionDetails"]))
    return msg_id + 1, details.get("value")


async def _read_page_state(ws: Any, msg_id: int, timeout: float) -> tuple[int, Dict[str, Any]]:
    next_id, value = await _evaluate_value(
        ws,
        msg_id,
        (
            "({url: location.href, title: document.title, "
            "readyState: document.readyState, "
            "textLen: ((document.body && document.body.innerText) || '').length})"
        ),
        timeout=timeout,
    )
    return next_id, value if isinstance(value, dict) else {}


async def _wait_for_page_content(
    ws: Any,
    msg_id: int,
    *,
    timeout: float,
    min_text_len: int = 40,
) -> tuple[int, Dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + max(2.0, timeout)
    latest: Dict[str, Any] = {}
    next_id = msg_id
    while asyncio.get_running_loop().time() < deadline:
        remaining = deadline - asyncio.get_running_loop().time()
        next_id, latest = await _read_page_state(
            ws,
            next_id,
            timeout=min(3.0, max(0.5, remaining)),
        )
        ready = str(latest.get("readyState") or "")
        text_len = int(latest.get("textLen") or 0)
        if ready in {"interactive", "complete"} and text_len >= min_text_len:
            break
        await asyncio.sleep(0.4)
    return next_id, latest


async def _capture_snapshot_on_ws(
    ws: Any,
    msg_id: int,
    *,
    compact: bool,
    timeout: float,
) -> tuple[int, Dict[str, Any]]:
    expr = _SNAPSHOT_JS.replace("__COMPACT__", "true" if compact else "false")
    next_id, value = await _evaluate_value(ws, msg_id, expr, timeout=timeout)
    if not isinstance(value, dict):
        return next_id, {"snapshot": "", "refs": {}}
    return next_id, value


async def _with_page_ws(cdp_url: str, timeout: float, handler):
    assert websockets is not None
    connect_timeout = min(10.0, max(3.0, timeout * 0.25))
    async with websockets.connect(
        cdp_url,
        max_size=10 * 1024 * 1024,
        open_timeout=connect_timeout,
        close_timeout=5,
        ping_interval=None,
    ) as ws:
        next_id = await _enable_page_runtime(ws, 1, timeout)
        return await handler(ws, next_id)


async def _navigate_page_async(
    cdp_url: str,
    url: str,
    timeout: float,
    *,
    include_snapshot: bool = True,
    compact_snapshot: bool = True,
) -> Dict[str, Any]:
    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        nav_result = await _send_and_wait(
            ws,
            msg_id,
            "Page.navigate",
            {"url": url},
            timeout=min(30.0, timeout),
        )
        error_text = str(nav_result.get("errorText") or "").strip()
        if error_text:
            return {"success": False, "error": f"Page.navigate failed: {error_text}"}

        wait_budget = max(5.0, timeout - 10.0)
        next_id, page_state = await _wait_for_page_content(
            ws,
            msg_id + 1,
            timeout=wait_budget,
            min_text_len=20 if url.rstrip("/").endswith(".json") else 40,
        )
        data: Dict[str, Any] = {
            "url": str(page_state.get("url") or url).strip(),
            "title": str(page_state.get("title") or "").strip(),
            "text_len": int(page_state.get("textLen") or 0),
        }
        if include_snapshot:
            _, snap = await _capture_snapshot_on_ws(
                ws,
                next_id,
                compact=compact_snapshot,
                timeout=min(15.0, wait_budget),
            )
            data["snapshot"] = str(snap.get("snapshot") or "")
            data["refs"] = snap.get("refs") or {}
            if not data["text_len"]:
                data["text_len"] = int(snap.get("text_len") or 0)
        return {"success": True, "data": data}

    return await _with_page_ws(cdp_url, timeout, _handler)


async def _capture_snapshot_async(
    cdp_url: str,
    *,
    compact: bool,
    timeout: float,
) -> Dict[str, Any]:
    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        _, snap = await _capture_snapshot_on_ws(
            ws,
            msg_id,
            compact=compact,
            timeout=min(20.0, timeout),
        )
        return {
            "success": True,
            "data": {
                "snapshot": str(snap.get("snapshot") or ""),
                "refs": snap.get("refs") or {},
                "url": str(snap.get("url") or ""),
                "title": str(snap.get("title") or ""),
            },
        }

    return await _with_page_ws(cdp_url, timeout, _handler)


async def _evaluate_async(cdp_url: str, expression: str, timeout: float) -> Dict[str, Any]:
    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        _, value = await _evaluate_value(ws, msg_id, expression, timeout=min(20.0, timeout))
        return {"success": True, "data": {"result": value}}

    return await _with_page_ws(cdp_url, timeout, _handler)


async def _click_ref_async(cdp_url: str, ref: str, timeout: float) -> Dict[str, Any]:
    safe_ref = json.dumps(ref)
    expr = (
        f"(() => {{"
        f"const el = document.querySelector('[data-hermes-ref=' + {safe_ref} + ']');"
        f"if (!el) return {{ok:false,error:'missing ref ' + {safe_ref}}};"
        f"el.click(); return {{ok:true}};"
        f"}})()"
    )

    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        _, value = await _evaluate_value(ws, msg_id, expr, timeout=min(10.0, timeout))
        if isinstance(value, dict) and value.get("ok"):
            return {"success": True, "data": {"clicked": ref}}
        err = (value or {}).get("error") if isinstance(value, dict) else "click failed"
        return {"success": False, "error": str(err)}

    return await _with_page_ws(cdp_url, timeout, _handler)


async def _scroll_async(
    cdp_url: str,
    direction: str,
    pixels: int,
    timeout: float,
) -> Dict[str, Any]:
    dy = pixels if direction == "down" else -pixels
    expr = f"window.scrollBy(0, {dy}); true"

    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        await _evaluate_value(ws, msg_id, expr, timeout=min(10.0, timeout))
        return {"success": True, "data": {"scrolled": direction}}

    return await _with_page_ws(cdp_url, timeout, _handler)


async def _back_async(cdp_url: str, timeout: float) -> Dict[str, Any]:
    async def _handler(ws: Any, msg_id: int) -> Dict[str, Any]:
        await _evaluate_value(ws, msg_id, "history.back(); true", timeout=min(10.0, timeout))
        return {"success": True, "data": {}}

    return await _with_page_ws(cdp_url, timeout, _handler)


def _dispatch(coro_factory, *args, **kwargs) -> Dict[str, Any]:
    if websockets is None:
        return {"success": False, "error": "websockets package is not installed"}
    try:
        return _run_async(coro_factory(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tab pool direct CDP command failed: %s", exc)
        return {"success": False, "error": str(exc)}


def navigate_open(cdp_url: str, url: str, *, timeout: float = 60.0) -> Dict[str, Any]:
    """Navigate a pooled page tab and return an agent-browser-compatible result."""
    if not cdp_url or not url:
        return {"success": False, "error": "cdp_url and url are required"}
    return _dispatch(
        _navigate_page_async,
        cdp_url,
        url,
        timeout,
        include_snapshot=True,
        compact_snapshot=True,
    )


def capture_snapshot(
    cdp_url: str,
    *,
    compact: bool = True,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Capture page snapshot via direct CDP."""
    if not cdp_url:
        return {"success": False, "error": "cdp_url is required"}
    return _dispatch(_capture_snapshot_async, cdp_url, compact=compact, timeout=timeout)


def evaluate(cdp_url: str, expression: str, *, timeout: float = 30.0) -> Dict[str, Any]:
    """Evaluate JavaScript on a pooled page tab."""
    if not cdp_url:
        return {"success": False, "error": "cdp_url is required"}
    return _dispatch(_evaluate_async, cdp_url, expression, timeout)


def click_ref(cdp_url: str, ref: str, *, timeout: float = 30.0) -> Dict[str, Any]:
    """Click an element previously tagged with ``data-hermes-ref``."""
    if not cdp_url or not ref:
        return {"success": False, "error": "cdp_url and ref are required"}
    return _dispatch(_click_ref_async, cdp_url, ref, timeout)


def scroll_page(
    cdp_url: str,
    direction: str,
    pixels: int = 500,
    *,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Scroll the pooled page tab."""
    if not cdp_url:
        return {"success": False, "error": "cdp_url is required"}
    if direction not in {"up", "down"}:
        return {"success": False, "error": f"invalid scroll direction: {direction}"}
    return _dispatch(_scroll_async, cdp_url, direction, pixels, timeout)


def history_back(cdp_url: str, *, timeout: float = 30.0) -> Dict[str, Any]:
    """Navigate back in the pooled tab's history."""
    if not cdp_url:
        return {"success": False, "error": "cdp_url is required"}
    return _dispatch(_back_async, cdp_url, timeout)


def run_direct_command(
    cdp_url: str,
    command: str,
    args: Optional[List[str]] = None,
    *,
    timeout: float = 30.0,
) -> Optional[Dict[str, Any]]:
    """Run a tab-pool browser command via page CDP. Returns None when unsupported."""
    args = args or []
    if command == "snapshot":
        compact = not args or "-c" in args
        return capture_snapshot(cdp_url, compact=compact, timeout=timeout)
    if command == "eval":
        if not args:
            return {"success": False, "error": "eval requires an expression"}
        return evaluate(cdp_url, args[0], timeout=timeout)
    if command == "click":
        if not args:
            return {"success": False, "error": "click requires a ref"}
        ref = args[0]
        if not ref.startswith("@"):
            ref = f"@{ref}"
        return click_ref(cdp_url, ref, timeout=timeout)
    if command == "scroll":
        direction = args[0] if args else "down"
        pixels = int(args[1]) if len(args) > 1 and str(args[1]).isdigit() else 500
        return scroll_page(cdp_url, direction, pixels, timeout=timeout)
    if command == "back":
        return history_back(cdp_url, timeout=timeout)
    return None


def navigation_landed_on_tab(expected_url: str, actual_url: str) -> bool:
    """Return True when ``actual_url`` reflects a successful ``expected_url`` nav."""
    actual = (actual_url or "").strip()
    expected = (expected_url or "").strip()
    if not actual or actual in {"about:blank", "chrome://new-tab-page/"}:
        return False
    if not expected:
        return False

    exp_base = expected.split("#", 1)[0].rstrip("/")
    act_base = actual.split("#", 1)[0].rstrip("/")
    if exp_base == act_base:
        return True
    exp_path = urlparse(exp_base).path.rstrip("/")
    act_path = urlparse(act_base).path.rstrip("/")
    if exp_path and exp_path == act_path:
        return True
    if "/explore/tags/" in exp_path and "instagram.com" in act_base:
        tag = exp_path.rsplit("/", 1)[-1]
        if tag and (
            tag in actual
            or f"q=%23{tag}" in actual
            or f"#{tag}" in actual
        ):
            return True
    exp_no_query = exp_base.split("?", 1)[0]
    act_no_query = act_base.split("?", 1)[0]
    return exp_no_query in act_no_query or act_no_query in exp_no_query
