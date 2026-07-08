"""Tests for rpa_fetch_google_serp extraction logic."""

from __future__ import annotations

import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

import pytest  # noqa: E402

from errors import DomChangedError  # noqa: E402
from google_serp import fetch_serp  # noqa: E402


class _FakeRunner:
    """Fake CDP runner that returns scripted navigate + eval results."""

    def __init__(self, eval_results):
        self._eval_results = list(eval_results)
        self.eval_calls = 0

    def navigate(self, url):
        return {"success": True, "data": {"url": url, "title": "test", "text_len": 100, "snapshot": ""}}

    def eval(self, js):
        self.eval_calls += 1
        if self.eval_calls - 1 < len(self._eval_results):
            return self._eval_results[self.eval_calls - 1]
        return None

    def scroll(self, *a, **k):
        pass


def test_fetch_serp_returns_results():
    runner = _FakeRunner([{"results": [{"rank": 1, "title": "A", "url": "https://a.com", "snippet": "s"}], "count": 1}])
    r = fetch_serp(runner, "test query", max_results=10)
    assert r["data"]["count"] == 1
    assert r["data"]["results"][0]["url"] == "https://a.com"
    assert r["data"]["query"] == "test query"


def test_fetch_serp_navigate_failure_raises():
    class _FailNav(_FakeRunner):
        def navigate(self, url):
            return {"success": False, "error": "timeout"}
    with pytest.raises(DomChangedError):
        fetch_serp(_FailNav([]), "q")


def test_fetch_serp_empty_results_returns_diagnostic():
    """When SERP JS returns 0 results, fetch_serp includes a diagnostic block
    so the agent can tell DOM-change from captcha/consent without a fallback."""
    diag = {"title": "q - Google 搜索", "hasCaptcha": False, "hasConsent": False, "h3Count": 0}
    runner = _FakeRunner([{"results": [], "count": 0}, diag])
    r = fetch_serp(runner, "q")
    assert r["data"]["count"] == 0
    # Second eval call is the diagnostic probe
    assert runner.eval_calls == 2
    assert r["data"]["diagnostic"] == diag


def test_fetch_serp_non_dict_result_raises():
    runner = _FakeRunner(["not a dict"])
    with pytest.raises(DomChangedError):
        fetch_serp(runner, "q")


def test_fetch_serp_truncates_to_max_results():
    big = [{"rank": i, "title": f"t{i}", "url": f"https://x{i}.com", "snippet": ""} for i in range(15)]
    runner = _FakeRunner([{"results": big, "count": 15}])
    r = fetch_serp(runner, "q", max_results=5)
    assert len(r["data"]["results"]) == 5
