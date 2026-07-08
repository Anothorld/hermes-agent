"""CDP runner — wraps tab_pool.acquire + cdp_page calls for RPA handlers.

Key design (from deep audit):
- ``acquire()`` returns ``{cdp_url, target_id}`` — only two fields.
- RPA direct ``tab_pool.acquire`` does NOT seed ``browser_tool._active_sessions``.
  If the same task later calls ``browser_*`` (fallback), browser_tool hooks
  would acquire again → double tab / stale cdp_url. We fix this by seeding
  ``_active_sessions`` in ``_seed_session()`` to match hooks' ``_make_session_info``.
- ``cdp_page.evaluate`` returns ``{success, data: {result: <python value>}}``
  — not a bare JS value. We unwrap.
- ``cdp_page.back`` does NOT exist — it's ``history_back``.
- ``scroll_page`` requires ``direction`` argument.
- No page-level concurrency lock — RPA handlers are synchronous and serial.

NOTE: ``local-chrome-tab-pool`` has hyphens in its directory name, so Python
cannot import it as a regular package. We use ``importlib.util`` to load
``tab_pool.py`` and ``cdp_page.py`` by absolute path, matching the pattern
used by ``local-chrome-tab-pool/hooks.py`` itself.

Each handler call = acquire (idempotent, cached) → navigate/eval → return.
Tab auto-recreated if released by idle reaper after ~300s.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

# Hyphenated directory can't use package imports for our own internal modules
_INTERNAL_DIR = str(Path(__file__).resolve().parent)
if _INTERNAL_DIR not in sys.path:
    sys.path.insert(0, _INTERNAL_DIR)

from errors import RpaError  # noqa: E402

logger = logging.getLogger(__name__)

# Path to local-chrome-tab-pool plugin's internal modules
_TAB_POOL_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2] / "local-chrome-tab-pool" / "internal"
)


def _load_tab_pool():
    """Load tab_pool module via importlib (hyphenated dir can't use package imports)."""
    cached = sys.modules.get("rpa_tab_pool")
    if cached is not None:
        return cached
    path = _TAB_POOL_PLUGIN_DIR / "tab_pool.py"
    if not path.exists():
        raise RpaError("tab_pool_not_found", f"local-chrome-tab-pool internal/tab_pool.py not found at {path}")
    spec = importlib.util.spec_from_file_location("rpa_tab_pool", path)
    if spec is None or spec.loader is None:
        raise RpaError("tab_pool_load_failed", f"Cannot load tab_pool from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rpa_tab_pool"] = module
    spec.loader.exec_module(module)
    return module


def _load_cdp_page():
    """Load cdp_page module via importlib."""
    cached = sys.modules.get("rpa_cdp_page")
    if cached is not None:
        return cached
    path = _TAB_POOL_PLUGIN_DIR / "cdp_page.py"
    if not path.exists():
        raise RpaError("cdp_page_not_found", f"local-chrome-tab-pool internal/cdp_page.py not found at {path}")
    spec = importlib.util.spec_from_file_location("rpa_cdp_page", path)
    if spec is None or spec.loader is None:
        raise RpaError("cdp_page_load_failed", f"Cannot load cdp_page from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rpa_cdp_page"] = module
    spec.loader.exec_module(module)
    return module


class CdpRunner:
    """Wrap tab_pool + cdp_page for one RPA handler invocation.

    All methods are synchronous and serial (no concurrent CDP calls on the
    same tab — tab-pool has no page-level lock).
    """

    def __init__(self, task_id: str, timeout: float = 30.0) -> None:
        self.task_id = task_id
        self.timeout = timeout

    def _acquire(self) -> dict:
        """Acquire (or reuse cached) tab for this task. Returns {cdp_url, target_id}."""
        tab_pool = _load_tab_pool()
        return tab_pool.acquire(self.task_id)

    def _seed_session(self, info: dict) -> None:
        """Sync browser_tool._active_sessions so browser_* fallback reuses this tab.

        Without this, a subsequent ``browser_navigate`` on the same task_id
        would acquire a second tab (hooks' _ensure_tab_pool_browser_session
        would not find an existing session and would call acquire again).
        We mirror the structure from local-chrome-tab-pool/hooks.py
        ``_make_session_info`` — including the hashed session_name and
        normalized task_id key.
        """
        try:
            from tools import browser_tool
            tab_pool = _load_tab_pool()
            norm_key = tab_pool.normalize_task_id(self.task_id)
            session_name = f"tabpool_{abs(hash(norm_key)) & 0xFFFF_FFFF:08x}"
            browser_tool._active_sessions[norm_key] = {
                "session_name": session_name,
                "bb_session_id": None,
                "cdp_url": info["cdp_url"],
                "features": {
                    "cdp_override": True,
                    "tab_pool": True,
                    "target_id": info.get("target_id", ""),
                    "tab_pool_owner": norm_key,
                },
            }
        except Exception as exc:
            logger.debug("cdp_runner: _seed_session best-effort failed: %s", exc)

    def navigate(self, url: str) -> dict:
        """Navigate the pooled tab to ``url``. Returns cdp_page response envelope.

        Returns:
            ``{success: bool, data?: {url, title, text_len, snapshot, refs}, error?: str}``
        """
        cdp_page = _load_cdp_page()
        info = self._acquire()
        self._seed_session(info)
        return cdp_page.navigate_open(info["cdp_url"], url, timeout=self.timeout)

    def eval(self, js: str) -> Any:
        """Evaluate JS on the pooled tab. Returns the raw Python value.

        Unlike ``cdp_page.evaluate`` which returns ``{success, data: {result}}``,
        this method unwraps to the bare JS value or raises ``RpaError``.

        Raises:
            RpaError: If CDP evaluation fails.
        """
        cdp_page = _load_cdp_page()
        info = self._acquire()
        self._seed_session(info)
        resp = cdp_page.evaluate(info["cdp_url"], js, timeout=self.timeout)
        if resp.get("success"):
            return resp["data"]["result"]
        raise RpaError("eval_failed", f"CDP evaluate failed: {resp.get('error')}")

    def click(self, ref: str) -> dict:
        """Click an element by ``@eXX`` ref. Returns cdp_page response."""
        cdp_page = _load_cdp_page()
        info = self._acquire()
        self._seed_session(info)
        return cdp_page.click_ref(info["cdp_url"], ref, timeout=self.timeout)

    def scroll(self, times: int = 1, direction: str = "down") -> None:
        """Scroll the page. ``direction`` is ``"down"`` or ``"up"``."""
        cdp_page = _load_cdp_page()
        info = self._acquire()
        self._seed_session(info)
        for _ in range(times):
            cdp_page.scroll_page(info["cdp_url"], direction, timeout=self.timeout)

    def back(self) -> dict:
        """Navigate back. Note: cdp_page function is ``history_back``, not ``back``."""
        cdp_page = _load_cdp_page()
        info = self._acquire()
        self._seed_session(info)
        return cdp_page.history_back(info["cdp_url"], timeout=self.timeout)

    def get_snapshot(self) -> str:
        """Get current page snapshot text (for risk detection)."""
        return str(self.eval("document.body ? document.body.innerText : ''") or "")
