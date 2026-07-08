"""Tests for risk_detector — checkpoint/captcha/login-wall detection."""

from __future__ import annotations

import errors
import risk_detector


def test_checkpoint_detected():
    assert risk_detector.detect_risk("Please verify your are human checkpoint") == "checkpoint"
    assert risk_detector.detect_risk("captcha required") == "checkpoint"
    assert risk_detector.detect_risk("Action Blocked") == "checkpoint"
    assert risk_detector.detect_risk("suspicious activity detected") == "checkpoint"


def test_rate_limited_detected():
    assert risk_detector.detect_risk("Try again later") == "rate_limited"
    assert risk_detector.detect_risk("Please wait a few minutes") == "rate_limited"


def test_session_expired_detected():
    assert risk_detector.detect_risk("Log in to Instagram") == "session_expired"
    assert risk_detector.detect_risk("Sign up to see more") == "session_expired"


def test_empty_render_detected():
    assert risk_detector.detect_risk("") == "empty_render"
    assert risk_detector.detect_risk(None) == "empty_render"


def test_normal_page_no_risk():
    assert risk_detector.detect_risk("125K Followers, 200 Following, 300 Posts - Home lover") is None
    assert risk_detector.detect_risk("Welcome to my home decor page! I share cozy living tips.") is None


def test_raise_on_risk_checkpoint():
    try:
        risk_detector.raise_on_risk("checkpoint detected")
        assert False, "should have raised CheckpointError"
    except errors.CheckpointError:
        pass


def test_raise_on_risk_rate_limited():
    try:
        risk_detector.raise_on_risk("Try again later")
        assert False, "should have raised RateLimitedError"
    except errors.RateLimitedError:
        pass


def test_raise_on_risk_session_expired():
    try:
        risk_detector.raise_on_risk("Log in to Instagram")
        assert False, "should have raised SessionExpiredError"
    except errors.SessionExpiredError:
        pass


def test_raise_on_risk_normal_text():
    # Normal text should not raise
    risk_detector.raise_on_risk("125K Followers, 200 Following")
