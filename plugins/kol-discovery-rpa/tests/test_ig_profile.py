"""Tests for ig_profile.fetch_profile — DOM-primary extraction + retry behavior.

Covers the "browser shows the profile with followers but RPA returns 0" bug:
followers must be read from the rendered <header> DOM (primary), with meta tags
as fallback, and a settle-retry must run when the SPA hasn't hydrated yet.
"""

from __future__ import annotations

import errors
import ig_profile
import pacing
import pytest


class _FakeRunner:
    """Minimal stand-in for CdpRunner — returns scripted navigate/eval results.

    Eval calls beyond the scripted list return ``{ok: false}`` so that
    best-effort flows like ``_fetch_account_location`` (which make extra eval
    calls after the main extraction) degrade gracefully instead of raising.
    """

    def __init__(self, task_id: str, eval_results, nav_resp=None):
        self.task_id = task_id
        self._eval_results = list(eval_results)
        self._nav_resp = nav_resp or {"success": True, "data": {"snapshot": ""}}
        self.eval_calls = 0

    def navigate(self, _url):
        return self._nav_resp

    def eval(self, _js):
        self.eval_calls += 1
        if self.eval_calls > len(self._eval_results):
            return {"ok": False, "error": "not scripted"}
        return self._eval_results[self.eval_calls - 1]


def _profile_result(**overrides):
    """Build a fake JS extraction result dict."""
    base = {
        "handle": "caitlinwilson",
        "full_name": "Caitlin Wilson",
        "bio": "Interior designer based in Los Angeles, CA",
        "followers_raw": "125K",
        "following_raw": "456",
        "posts_count_raw": "789",
        "is_verified": False,
        "is_business": None,
        "professional_category": "",
        "location_signals": ["los angeles", "ca", "los angeles ca"],
        "bio_links": ["https://linktr.ee/caitlinwilson"],
        "external_url": "https://linktr.ee/caitlinwilson",
        "page_text_sample": "Caitlin Wilson 789 posts 125K followers 456 following",
        "extraction_source": "dom",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _stub_pacing_and_sleep(monkeypatch):
    """Pacing jitter (2-4s) and the retry settle sleep would slow tests down."""
    monkeypatch.setattr(pacing, "jitter_delay", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "mark_profile", lambda *a, **k: None)
    # Null out time.sleep used by the settle-retry in fetch_profile.
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *a, **k: None)


# --------------------------------------------------------------- DOM extraction

def test_dom_extraction_is_primary():
    """Followers present in DOM → followers_raw captured, source='dom_word'."""
    runner = _FakeRunner("t1", [_profile_result(followers_raw="523K", extraction_source="dom_word")])
    out = ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert out["data"]["followers_raw"] == "523K"
    assert out["data"]["extraction_source"] == "dom_word"
    assert out["qualification"]["hard_discard"] is False
    assert runner.eval_calls == 1  # no retry needed


def test_meta_fallback_when_dom_empty():
    """DOM empty but meta description has followers → use meta, no hard_discard."""
    runner = _FakeRunner(
        "t1",
        [_profile_result(
            followers_raw="487K",
            extraction_source="meta_description",
            page_text_sample="Some IG shell text without a follower word",
            location_signals=[],
            bio="",
        )],
    )
    out = ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert out["data"]["followers_raw"] == "487K"
    assert out["data"]["extraction_source"] == "meta_description"
    # No follower signal word → no retry attempted
    assert runner.eval_calls == 1


def test_structural_fallback_extraction_source():
    """Structural (locale-independent) extraction reports source='dom_structural'."""
    runner = _FakeRunner("t1", [_profile_result(followers_raw="4.5万", extraction_source="dom_structural")])
    out = ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert out["data"]["followers_raw"] == "4.5万"
    assert out["data"]["extraction_source"] == "dom_structural"


# --------------------------------------------------------------- locale coverage

def test_chinese_locale_follower_signal_triggers_retry():
    """zh-CN page ('粉丝' in text) with empty first eval → retry fires."""
    empty_shell = _profile_result(
        followers_raw="",
        extraction_source="",
        page_text_sample="caitlinwilson 2361帖子 粉丝 3571关注 (not hydrated)",
        location_signals=[],
        bio="",
    )
    hydrated = _profile_result(followers_raw="4.5万", extraction_source="dom_word")
    runner = _FakeRunner("t1", [empty_shell, hydrated])
    out = ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert out["data"]["followers_raw"] == "4.5万"
    assert runner.eval_calls == 2


def test_chinese_locale_retry_still_empty_raises():
    """zh-CN page, retry still yields no followers → raise DomChangedError."""
    empty_shell = _profile_result(
        followers_raw="",
        extraction_source="",
        page_text_sample="caitlinwilson 2361帖子 粉丝 3571关注 (broken)",
        location_signals=[],
        bio="",
    )
    runner = _FakeRunner("t1", [empty_shell, empty_shell])
    with pytest.raises(errors.DomChangedError):
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert runner.eval_calls == 2


# --------------------------------------------------------------- hydration retry

def test_settle_retry_recovers_followers():
    """First eval empty + page mentions 'followers' → retry yields followers."""
    empty_shell = _profile_result(
        followers_raw="",
        extraction_source="",
        page_text_sample="caitlinwilson followers following posts (shell not hydrated)",
        location_signals=[],
        bio="",
    )
    hydrated = _profile_result(followers_raw="512K", extraction_source="dom_word")
    runner = _FakeRunner("t1", [empty_shell, hydrated])
    out = ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert out["data"]["followers_raw"] == "512K"
    assert out["data"]["extraction_source"] == "dom_word"
    assert runner.eval_calls == 2
    assert out["qualification"]["hard_discard"] is False


def test_settle_retry_still_empty_raises_dom_changed():
    """Retry still yields no followers despite 'followers' in page text → raise."""
    empty_shell = _profile_result(
        followers_raw="",
        extraction_source="",
        page_text_sample="caitlinwilson 789 posts followers 456 following (broken selectors)",
        location_signals=[],
        bio="",
    )
    runner = _FakeRunner("t1", [empty_shell, empty_shell])
    with pytest.raises(errors.DomChangedError) as exc:
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert "not extractable" in str(exc.value).lower() or "selector" in str(exc.value).lower()
    assert runner.eval_calls == 2


# --------------------------------------------------------------- render detection

def test_empty_render_raises():
    """Blank page (no text, no followers) → DomChangedError, not silent 0."""
    runner = _FakeRunner(
        "t1",
        [_profile_result(
            followers_raw="",
            extraction_source="",
            page_text_sample="",
            location_signals=[],
            bio="",
        )],
    )
    with pytest.raises(errors.DomChangedError) as exc:
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert "rendered empty" in str(exc.value).lower()
    assert runner.eval_calls == 1


def test_login_wall_no_followers_word_raises():
    """Page has text but no 'followers' word and no count → likely login wall → raise."""
    runner = _FakeRunner(
        "t1",
        [_profile_result(
            followers_raw="",
            extraction_source="",
            page_text_sample="Sign up Log in Instagram ... get the app",
            location_signals=[],
            bio="",
        )],
    )
    with pytest.raises(errors.DomChangedError) as exc:
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert "follower section" in str(exc.value).lower() or "login" in str(exc.value).lower()
    # No "followers" word → no retry
    assert runner.eval_calls == 1


def test_navigate_failure_raises():
    """navigate returns success=False → DomChangedError with the nav error."""
    runner = _FakeRunner(
        "t1",
        [_profile_result()],
        nav_resp={"success": False, "error": "net::ERR_CONNECTION_RESET"},
    )
    with pytest.raises(errors.DomChangedError) as exc:
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)
    assert "navigate failed" in str(exc.value).lower()


def test_non_dict_eval_raises():
    """eval returns a non-dict → DomChangedError."""
    runner = _FakeRunner("t1", [None])
    with pytest.raises(errors.DomChangedError):
        ig_profile.fetch_profile(runner, "caitlinwilson", include_account_location=False)


# --------------------------------------------------------------- qualification wiring

def test_followers_below_min_hard_discard():
    """A real sub-100k profile (followers extracted) still hard_discards."""
    runner = _FakeRunner(
        "t1",
        [_profile_result(followers_raw="4.6万", location_signals=["US"], bio="decor")],
    )
    out = ig_profile.fetch_profile(runner, "smallkol", include_account_location=False)
    assert out["data"]["followers_raw"] == "4.6万"
    assert out["qualification"]["hard_discard"] is True
    assert "followers_below_100k" in out["qualification"]["discard_reasons"]


def test_handle_strips_at_prefix():
    """Leading @ in handle is stripped and reflected in profile data."""
    runner = _FakeRunner("t1", [_profile_result(handle="caitlinwilson")])
    out = ig_profile.fetch_profile(runner, "@caitlinwilson", include_account_location=False)
    assert out["data"]["handle"] == "caitlinwilson"
    assert out["data"]["profile_url"].endswith("/caitlinwilson/")


# --------------------------------------------------------------- account location

def _location_eval_sequence(location_value="美国"):
    """Scripted eval results simulating the '...' → '账户简介' click flow.

    Order: [profile_extraction, click_options, click_about, read_details, close].
    """
    profile = _profile_result(followers_raw="125K", location_signals=[])
    return [
        profile,
        {"ok": True},  # click '...' succeeded
        {"ok": True, "via": "账户简介"},  # click '账户简介' succeeded
        {"ok": True, "location": location_value, "date_joined": "2011年4月",
         "verified_date": "2023年3月", "former_usernames": "3"},
        {"ok": True, "via": "button"},  # close dialog
    ]


def test_account_location_us_passes_region_gate():
    """Account dialog says '美国' → account_country=US → region gate passes."""
    runner = _FakeRunner("t1", _location_eval_sequence("美国"))
    out = ig_profile.fetch_profile(runner, "caitlinwilson")  # default include_account_location=True
    assert out["data"]["account_location"] == "美国"
    assert out["data"]["account_country"] == "US"
    assert out["data"]["account_date_joined"] == "2011年4月"
    # Region gate uses the authoritative US → passes
    assert out["qualification"]["gates"]["region"]["pass"] is True
    assert out["qualification"]["gates"]["region"]["value"] == "US"
    assert "region_unknown" not in out["qualification"]["discard_reasons"]


def test_account_location_canada_passes_region_gate():
    """Account dialog says '加拿大' → account_country=CA → region gate passes (CA allowed)."""
    runner = _FakeRunner("t1", _location_eval_sequence("加拿大"))
    out = ig_profile.fetch_profile(runner, "somehandle")
    assert out["data"]["account_country"] == "CA"
    assert out["qualification"]["gates"]["region"]["pass"] is True
    assert out["qualification"]["gates"]["region"]["value"] == "CA"


def test_account_location_uk_discards():
    """Account dialog says '英国' (GB) → not US/CA → region gate fails (correct discard)."""
    runner = _FakeRunner("t1", _location_eval_sequence("英国"))
    out = ig_profile.fetch_profile(runner, "somehandle")
    assert out["data"]["account_country"] == "GB"
    assert out["qualification"]["gates"]["region"]["pass"] is False
    assert "region_unknown" in out["qualification"]["discard_reasons"]


def test_account_location_overrides_bio_false_positive():
    """Account country (US) must override a bio 'ca' false-positive (Canada).

    A bio containing 'ca' as a substring used to resolve to Canada; the
    authoritative account-dialog country wins because it's prepended to the
    region signals and _resolve_region returns on the first definitive match.
    """
    runner = _FakeRunner("t1", _location_eval_sequence("美国"))
    # Inject a bio-derived 'ca' signal that would wrongly resolve to Canada
    profile = _profile_result(followers_raw="125K", location_signals=["ca"], bio="soCal life")
    runner._eval_results[0] = profile
    out = ig_profile.fetch_profile(runner, "somehandle")
    assert out["data"]["account_country"] == "US"
    assert out["qualification"]["gates"]["region"]["value"] == "US"
    assert out["qualification"]["gates"]["region"]["pass"] is True


def test_account_location_flow_failure_falls_back_to_bio():
    """If the '...' click fails, location is empty and region falls back to bio signals."""
    runner = _FakeRunner("t1", [
        _profile_result(followers_raw="125K", location_signals=["Los Angeles"]),
        {"ok": False, "error": "options button not found"},  # click '...' fails
    ])
    out = ig_profile.fetch_profile(runner, "somehandle")
    assert out["data"]["account_location"] == ""
    assert out["data"]["account_country"] == ""
    # Region falls back to bio 'Los Angeles' → US
    assert out["qualification"]["gates"]["region"]["value"] == "US"


def test_account_location_disabled_skips_click_flow():
    """include_account_location=False → no extra eval calls, account_country empty."""
    runner = _FakeRunner("t1", [_profile_result(followers_raw="125K", location_signals=[])])
    out = ig_profile.fetch_profile(runner, "somehandle", include_account_location=False)
    assert out["data"]["account_country"] == ""
    assert runner.eval_calls == 1  # only the profile extraction


def test_normalize_country_locale_variants():
    """Country normalization handles zh/en/ja/ko variants."""
    assert ig_profile._normalize_country("美国") == "US"
    assert ig_profile._normalize_country("United States") == "US"
    assert ig_profile._normalize_country("CANADA") == "CA"
    assert ig_profile._normalize_country("日本") == "JP"
    assert ig_profile._normalize_country("") == ""
    assert ig_profile._normalize_country("Atlantis") == ""
