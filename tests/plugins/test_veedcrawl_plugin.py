"""Tests for the bundled veedcrawl plugin.

Uses ``httpx.MockTransport`` (the established house pattern in this repo) to
exercise the client without hitting the network. Cache files are isolated
under a per-test ``HERMES_HOME`` to keep runs hermetic.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import httpx
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "veedcrawl"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_hermes_home(tmp_path, monkeypatch):
    """Redirect ``~/.hermes`` so caches don't leak between tests or developers."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("VEEDCRAWL_API_KEY", "vc_test_key")
    yield home


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs):
    from plugins.veedcrawl.client import VeedcrawlClient
    return VeedcrawlClient(
        api_key="vc_test_key",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        now=kwargs.pop("now", lambda: 0.0),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Manifest + plugin layout
# ---------------------------------------------------------------------------

class TestManifest:
    def test_directory_layout(self):
        assert PLUGIN_DIR.is_dir()
        assert (PLUGIN_DIR / "plugin.yaml").is_file()
        assert (PLUGIN_DIR / "README.md").is_file()
        assert (PLUGIN_DIR / "__init__.py").is_file()
        assert (PLUGIN_DIR / "client.py").is_file()
        assert (PLUGIN_DIR / "tools.py").is_file()
        assert (PLUGIN_DIR / "_internal").is_dir()

    def test_manifest_advertises_six_tools(self):
        data = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
        assert data["name"] == "veedcrawl"
        assert data["kind"] == "backend"
        assert set(data["provides_tools"]) == {
            "veedcrawl_account",
            "veedcrawl_metadata",
            "veedcrawl_transcript",
            "veedcrawl_extract",
            "veedcrawl_profile",
            "veedcrawl_job",
        }


# ---------------------------------------------------------------------------
# Auth resolution + check_fn gate
# ---------------------------------------------------------------------------

class TestAuthGate:
    def test_resolve_api_key_prefers_dedicated_var(self, monkeypatch):
        from plugins.veedcrawl.client import resolve_api_key

        monkeypatch.setenv("VEEDCRAWL_API_KEY", "primary")
        monkeypatch.setenv("X_API_KEY", "fallback")
        assert resolve_api_key() == "primary"

    def test_resolve_api_key_falls_back(self, monkeypatch):
        from plugins.veedcrawl.client import resolve_api_key

        monkeypatch.delenv("VEEDCRAWL_API_KEY", raising=False)
        monkeypatch.setenv("X_API_KEY", "fallback")
        assert resolve_api_key() == "fallback"

    def test_check_fn_blocks_when_no_key(self, monkeypatch):
        from plugins.veedcrawl.tools import _check_veedcrawl_available

        monkeypatch.delenv("VEEDCRAWL_API_KEY", raising=False)
        monkeypatch.delenv("X_API_KEY", raising=False)
        assert _check_veedcrawl_available() is False

    def test_check_fn_allows_when_key_set(self):
        from plugins.veedcrawl.tools import _check_veedcrawl_available

        # The autouse fixture sets VEEDCRAWL_API_KEY for us.
        assert _check_veedcrawl_available() is True

    def test_construct_without_key_raises_auth(self, monkeypatch):
        monkeypatch.delenv("VEEDCRAWL_API_KEY", raising=False)
        monkeypatch.delenv("X_API_KEY", raising=False)
        from plugins.veedcrawl.client import VeedcrawlClient
        from plugins.veedcrawl._internal.errors import VeedcrawlAuthRequiredError

        with pytest.raises(VeedcrawlAuthRequiredError):
            VeedcrawlClient()


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------

class TestHTTPErrorMapping:
    def test_401_maps_to_auth_error(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlAuthRequiredError

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "bad key"})

        with _client(handler) as c, pytest.raises(VeedcrawlAuthRequiredError):
            c.me(force=True)

    def test_403_maps_to_insufficient_credits(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlInsufficientCreditsError

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "out of credits"})

        with _client(handler) as c, pytest.raises(VeedcrawlInsufficientCreditsError):
            c.me(force=True)

    def test_504_maps_to_job_timeout(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlJobTimeoutError

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(504, json={"message": "gateway timeout"})

        with _client(handler) as c, pytest.raises(VeedcrawlJobTimeoutError):
            c.me(force=True)

    def test_429_retries_once_then_succeeds(self):
        calls: list[int] = []

        def handler(_: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    json={"message": "slow"},
                    headers={"X-RateLimit-Reset": "1", "Retry-After": "1"},
                )
            return httpx.Response(
                200,
                json={"creditsRemaining": 100},
                headers={"X-RateLimit-Remaining": "59"},
            )

        with _client(handler) as c:
            payload = c.me(force=True)
        assert payload["creditsRemaining"] == 100
        assert len(calls) == 2

    def test_two_429s_surface_rate_limit_error(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlRateLimitError

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "slow"}, headers={"X-RateLimit-Reset": "0"})

        with _client(handler) as c, pytest.raises(VeedcrawlRateLimitError):
            c.me(force=True)


# ---------------------------------------------------------------------------
# Metadata + cache behaviour
# ---------------------------------------------------------------------------

class TestMetadataCache:
    def test_metadata_caches_response_and_force_refresh_bypasses(self):
        calls: list[int] = []

        def handler(_: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"title": f"v{len(calls)}"})

        with _client(handler) as c:
            first = c.metadata("https://youtu.be/abc")
            second = c.metadata("https://youtu.be/abc")
            third = c.metadata("https://youtu.be/abc", force_refresh=True)

        assert first["title"] == "v1"
        assert second["_cached"] is True
        assert second["title"] == "v1"
        assert third["title"] == "v2"
        assert len(calls) == 2  # one initial + one force-refresh


# ---------------------------------------------------------------------------
# Async-job orchestration
# ---------------------------------------------------------------------------

def _credits_then(routes: dict[str, list[httpx.Response]]):
    """Build a handler that returns canned responses per (method, path)."""
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        bucket = routes.get(key)
        if not bucket:
            return httpx.Response(500, json={"message": f"no route for {key}"})
        return bucket.pop(0) if len(bucket) > 1 else bucket[0]
    return handler


class TestAsyncJobs:
    def test_extract_polls_until_completed_and_caches(self):
        routes = {
            "GET /v1/me": [httpx.Response(200, json={"creditsRemaining": 1000})],
            "POST /v1/extract": [httpx.Response(202, json={"jobId": "job-1", "status": "queued"})],
            "GET /v1/extract/job-1": [
                httpx.Response(200, json={"jobId": "job-1", "status": "processing"}),
                httpx.Response(200, json={"jobId": "job-1", "status": "completed", "resultJson": {"score": 7}}),
                # extra GET after polling for fresh headers
                httpx.Response(200, json={"jobId": "job-1", "status": "completed", "resultJson": {"score": 7}}),
            ],
        }

        with _client(_credits_then(routes)) as c:
            result = c.extract(url="https://x.com/y", prompt="score")

        assert result["status"] == "completed"
        assert result["result_json"] == {"score": 7}
        assert result["credits_used"] == 10
        assert result["_cached"] is False

        # Second call hits the permanent cache.
        with _client(_credits_then({
            "GET /v1/me": [httpx.Response(200, json={"creditsRemaining": 1000})],
        })) as c:
            cached = c.extract(url="https://x.com/y", prompt="score")
        assert cached["_cached"] is True
        assert cached["result_json"] == {"score": 7}

    def test_extract_failed_job_raises(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlJobFailedError

        routes = {
            "GET /v1/me": [httpx.Response(200, json={"creditsRemaining": 1000})],
            "POST /v1/extract": [httpx.Response(202, json={"jobId": "job-2", "status": "queued"})],
            "GET /v1/extract/job-2": [
                httpx.Response(200, json={
                    "jobId": "job-2",
                    "status": "failed",
                    "error": {"code": "transcription_failed", "message": "no audio"},
                }),
            ],
        }
        with _client(_credits_then(routes)) as c, pytest.raises(VeedcrawlJobFailedError) as ei:
            c.extract(url="https://x.com/z", prompt="score")
        assert ei.value.details["job_id"] == "job-2"

    def test_extract_blocked_by_credit_guardrail(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlInsufficientCreditsError

        routes = {
            "GET /v1/me": [httpx.Response(200, json={"creditsRemaining": 5})],  # need 10 * 2 = 20
        }
        with _client(_credits_then(routes)) as c, pytest.raises(VeedcrawlInsufficientCreditsError) as ei:
            c.extract(url="https://x.com/y", prompt="score")
        assert ei.value.details["credits_needed"] == 10
        assert ei.value.details["credits_remaining"] == 5

    def test_wait_false_returns_job_id_immediately(self):
        routes = {
            "GET /v1/me": [httpx.Response(200, json={"creditsRemaining": 1000})],
            "POST /v1/transcript": [httpx.Response(202, json={"jobId": "job-3", "status": "queued"})],
        }
        with _client(_credits_then(routes)) as c:
            result = c.transcript(url="https://x.com/a", mode="generate", wait=False)
        assert result == {
            "job_id": "job-3",
            "status": "queued",
            "_rate_limit_remaining": None,
            "_cached": False,
        }


# ---------------------------------------------------------------------------
# tools.py handler integration
# ---------------------------------------------------------------------------

class TestToolHandlers:
    def test_handler_returns_tool_error_json_on_auth_failure(self, monkeypatch):
        monkeypatch.delenv("VEEDCRAWL_API_KEY", raising=False)
        monkeypatch.delenv("X_API_KEY", raising=False)
        from plugins.veedcrawl.tools import _handle_metadata

        raw = _handle_metadata({"url": "https://youtu.be/abc"})
        payload = json.loads(raw)
        assert payload["error"]
        assert payload["code"] == "auth"

    def test_handler_returns_tool_result_on_success(self, monkeypatch):
        # Patch the client class so the handler uses our mock transport.
        import plugins.veedcrawl.tools as tools_mod
        from plugins.veedcrawl.client import VeedcrawlClient

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"title": "demo"})

        def factory():
            return VeedcrawlClient(
                api_key="vc_test_key",
                transport=httpx.MockTransport(handler),
                sleep=lambda _: None,
            )

        monkeypatch.setattr(tools_mod, "VeedcrawlClient", lambda: factory())

        raw = tools_mod._handle_metadata({"url": "https://youtu.be/xyz"})
        payload = json.loads(raw)
        assert payload["title"] == "demo"


# ---------------------------------------------------------------------------
# job_id-only resume / lookup paths
# ---------------------------------------------------------------------------

class TestJobLookup:
    """Veedcrawl async jobs must be retrievable by ``job_id`` alone.

    Without this, an agent that loses the result payload of a successful
    extract/transcript call would have to re-spend credits to re-fetch it.
    """

    def _patch_factory(self, monkeypatch, handler):
        import plugins.veedcrawl.tools as tools_mod
        from plugins.veedcrawl.client import VeedcrawlClient

        def factory():
            return VeedcrawlClient(
                api_key="vc_test_key",
                transport=httpx.MockTransport(handler),
                sleep=lambda _: None,
            )

        monkeypatch.setattr(tools_mod, "VeedcrawlClient", lambda: factory())
        return tools_mod

    def test_client_lookup_job_returns_completed_payload(self):
        routes = {
            "GET /v1/extract/job-x": [
                httpx.Response(200, json={
                    "jobId": "job-x",
                    "status": "completed",
                    "resultJson": {"score": 9},
                }),
            ],
        }
        with _client(_credits_then(routes)) as c:
            result = c.lookup_job(endpoint="extract", job_id="job-x")
        assert result["status"] == "completed"
        assert result["result_json"] == {"score": 9}
        # cost is recorded for accounting parity with a fresh extract.
        assert result["credits_used"] == 10

    def test_client_lookup_job_unknown_endpoint_raises(self):
        from plugins.veedcrawl._internal.errors import VeedcrawlAPIError

        with _client(lambda _: httpx.Response(500)) as c, pytest.raises(VeedcrawlAPIError):
            c.lookup_job(endpoint="bogus", job_id="abc")

    def test_extract_handler_resume_by_job_id_only(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/v1/extract/job-y"
            return httpx.Response(200, json={
                "jobId": "job-y",
                "status": "completed",
                "resultJson": {"k": "v"},
            })

        tools_mod = self._patch_factory(monkeypatch, handler)
        raw = tools_mod._handle_extract({"job_id": "job-y"})
        payload = json.loads(raw)
        assert payload["status"] == "completed"
        assert payload["result_json"] == {"k": "v"}

    def test_transcript_handler_resume_by_job_id_only(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/v1/transcript/job-z"
            return httpx.Response(200, json={
                "jobId": "job-z",
                "status": "completed",
                "resultJson": {"text": "hello"},
            })

        tools_mod = self._patch_factory(monkeypatch, handler)
        raw = tools_mod._handle_transcript({"job_id": "job-z"})
        payload = json.loads(raw)
        assert payload["status"] == "completed"

    def test_veedcrawl_job_tool_dispatches_by_endpoint(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/extract/job-q"
            return httpx.Response(200, json={
                "jobId": "job-q",
                "status": "completed",
                "resultJson": {"ok": True},
            })

        tools_mod = self._patch_factory(monkeypatch, handler)
        raw = tools_mod._handle_job({"endpoint": "extract", "job_id": "job-q"})
        payload = json.loads(raw)
        assert payload["result_json"] == {"ok": True}

    def test_extract_missing_url_and_job_id_returns_bad_request(self, monkeypatch):
        from plugins.veedcrawl import tools as tools_mod

        # no transport call should ever happen — fail loud if it does.
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("HTTP must not be invoked when args are invalid")

        self._patch_factory(monkeypatch, handler)
        raw = tools_mod._handle_extract({})
        payload = json.loads(raw)
        assert payload["error"]
        assert payload["code"] == "bad_request"
        assert "url" in payload["error"] or "job_id" in payload["error"]

    def test_extract_missing_prompt_returns_bad_request(self, monkeypatch):
        from plugins.veedcrawl import tools as tools_mod

        self._patch_factory(monkeypatch, lambda _: httpx.Response(500))
        raw = tools_mod._handle_extract({"url": "https://x.com/y"})
        payload = json.loads(raw)
        assert payload["code"] == "bad_request"
        assert "prompt" in payload["error"]

    def test_metadata_missing_url_returns_bad_request(self, monkeypatch):
        from plugins.veedcrawl import tools as tools_mod

        self._patch_factory(monkeypatch, lambda _: httpx.Response(500))
        raw = tools_mod._handle_metadata({"job_id": "wont-help"})
        payload = json.loads(raw)
        assert payload["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

class TestCacheLayer:
    def test_round_trip(self):
        from plugins.veedcrawl._internal import cache

        cache.put("metadata", {"url": "u"}, {"x": 1}, ttl_s=10)
        assert cache.get("metadata", {"url": "u"}) == {"x": 1}

    def test_ttl_expiry(self, monkeypatch):
        from plugins.veedcrawl._internal import cache

        cache.put("metadata", {"url": "v"}, {"x": 2}, ttl_s=1)
        # Fast-forward time well past the TTL.
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 10)
        assert cache.get("metadata", {"url": "v"}) is None

    def test_permanent_entry_never_expires(self, monkeypatch):
        from plugins.veedcrawl._internal import cache

        cache.put("extract", {"k": "v"}, {"x": 3}, ttl_s=None)
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 10**9)
        assert cache.get("extract", {"k": "v"}) == {"x": 3}


# ---------------------------------------------------------------------------
# Poller backoff
# ---------------------------------------------------------------------------

class TestPollerBackoff:
    def test_backoff_respects_total_budget(self):
        from plugins.veedcrawl._internal.poller import backoff_sequence

        seq = backoff_sequence(timeout_s=4.0)
        # Schedule starts 1, 2, 3, ... — at budget 4 we should fit 1+2+1 (clipped).
        assert sum(seq) <= 4.0
        assert seq[0] == 1.0

    def test_poll_returns_terminal_payload(self):
        from plugins.veedcrawl._internal.poller import poll

        states = iter([
            {"status": "queued"},
            {"status": "processing"},
            {"status": "completed", "x": 1},
        ])
        result = poll(lambda: next(states), timeout_s=10.0, sleep=lambda _: None)
        assert result == {"status": "completed", "x": 1}

    def test_poll_raises_timeout_when_never_terminal(self):
        from plugins.veedcrawl._internal.poller import poll

        with pytest.raises(TimeoutError):
            poll(lambda: {"status": "processing"}, timeout_s=0.5, sleep=lambda _: None)
