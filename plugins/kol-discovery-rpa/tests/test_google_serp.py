"""Tests for rpa_fetch_google_serp extraction logic."""

from __future__ import annotations

import sys
from pathlib import Path

_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
if str(_INTERNAL) not in sys.path:
    sys.path.insert(0, str(_INTERNAL))

import pytest  # noqa: E402

from errors import DomChangedError  # noqa: E402
import pacing  # noqa: E402
from google_serp import (  # noqa: E402
    collect_content_shortcodes,
    extract_ig_handles_from_serp,
    fetch_serp,
    resolve_content_authors,
)


@pytest.fixture(autouse=True)
def _no_pacing_sleep(monkeypatch):
    """Keep SERP unit tests fast (no profile/reel jitter / quota)."""
    monkeypatch.setattr(pacing, "jitter_delay", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "mark_reel_load", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)


class _FakeRunner:
    """Fake CDP runner that returns scripted navigate + eval results."""

    def __init__(self, eval_results, author_by_code=None):
        self._eval_results = list(eval_results)
        self._author_by_code = dict(author_by_code or {})
        self.eval_calls = 0
        self.navigated = []

    def navigate(self, url):
        self.navigated.append(url)
        return {"success": True, "data": {"url": url, "title": "test", "text_len": 100, "snapshot": ""}}

    def eval(self, js):
        # Author-resolve JS path (lightweight handle extract).
        if "og:url" in js and "dom_link" in js:
            last = self.navigated[-1] if self.navigated else ""
            m = __import__("re").search(r"/p/([A-Za-z0-9_-]+)/", last)
            code = m.group(1) if m else ""
            handle = self._author_by_code.get(code, "")
            return {"handle": handle, "source": "url" if handle else None}
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


def test_extract_ig_handles_from_profile_url_and_mentions():
    rows = [
        {
            "rank": 1,
            "title": "Best home creators",
            "url": "https://www.instagram.com/home_ec_op/",
            "snippet": "Follow @home_ec_op and also @weworewhat for lifestyle",
        },
        {
            "rank": 2,
            "title": "Reel",
            "url": "https://www.instagram.com/reel/DXXX123/",
            "snippet": "no handle in path",
        },
        {
            "rank": 3,
            "title": "@cationz apartment tour",
            "url": "https://blog.example/list",
            "snippet": "US creator",
        },
    ]
    handles = extract_ig_handles_from_serp(rows)
    got = [h["handle"] for h in handles]
    assert got[0] == "home_ec_op"
    assert "weworewhat" in got
    assert "cationz" in got
    assert "reel" not in got
    assert "dxxx123" not in got


def test_fetch_serp_includes_candidate_handles():
    rows = [{
        "rank": 1,
        "title": "@okdeon photos",
        "url": "https://www.instagram.com/okdeon/",
        "snippet": "designer",
    }]
    runner = _FakeRunner([{"results": rows, "count": 1}])
    r = fetch_serp(runner, "okdeon instagram", max_results=10, resolve_authors=False)
    assert r["data"]["candidate_handle_count"] >= 1
    assert r["data"]["candidate_handles"][0]["handle"] == "okdeon"


def test_collect_content_shortcodes_from_reel_urls():
    rows = [
        {"url": "https://www.instagram.com/reel/ABC123/"},
        {"url": "https://www.instagram.com/p/XYZ999/?hl=en"},
        {"url": "https://www.instagram.com/reel/ABC123/"},  # dup
        {"url": "https://www.instagram.com/okdeon/"},
    ]
    assert collect_content_shortcodes(rows) == ["ABC123", "XYZ999"]


def test_resolve_content_authors_dedupes_handles():
    runner = _FakeRunner([], author_by_code={"AAA": "home_tour", "BBB": "home_tour", "CCC": "okdeon"})
    out = resolve_content_authors(runner, ["AAA", "BBB", "CCC"], max_resolve=10)
    handles = [a["handle"] for a in out["authors"]]
    assert handles == ["home_tour", "okdeon"]
    assert out["navigations"] == 3


def test_fetch_serp_resolves_reel_authors_into_candidates():
    rows = [
        {
            "rank": 1,
            "title": "Apartment tour",
            "url": "https://www.instagram.com/reel/REEL1/",
            "snippet": "no @mention",
        },
        {
            "rank": 2,
            "title": "Another reel",
            "url": "https://www.instagram.com/reel/REEL2/",
            "snippet": "",
        },
    ]
    runner = _FakeRunner(
        [{"results": rows, "count": 2}],
        author_by_code={"REEL1": "sofahunter", "REEL2": "nestmaker"},
    )
    r = fetch_serp(runner, "home tour instagram", max_results=10, resolve_authors=True)
    handles = {h["handle"] for h in r["data"]["candidate_handles"]}
    assert "sofahunter" in handles
    assert "nestmaker" in handles
    assert r["data"]["authors_resolved"] == 2
    assert r["data"]["content_urls_found"] == 2
    assert r["data"]["author_navigations"] == 2
