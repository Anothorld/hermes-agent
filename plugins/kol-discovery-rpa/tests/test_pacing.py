"""Tests for pacing — jitter sleep + per-run quota."""

from __future__ import annotations

import errors
import pacing


def test_quota_starts_at_zero():
    pacing.reset("test_task_1")
    snap = pacing.quota_snapshot("test_task_1")
    assert snap["profiles_used"] == 0
    assert snap["reel_loads_used"] == 0


def test_mark_profile_increments():
    pacing.reset("test_task_2")
    pacing.mark_profile("test_task_2")
    pacing.mark_profile("test_task_2")
    assert pacing.quota_snapshot("test_task_2")["profiles_used"] == 2


def test_mark_reel_load_increments():
    pacing.reset("test_task_3")
    pacing.mark_reel_load("test_task_3")
    assert pacing.quota_snapshot("test_task_3")["reel_loads_used"] == 1


def test_profile_quota_exceeded():
    pacing.reset("quota_test_profile")
    for _ in range(40):
        pacing.mark_profile("quota_test_profile")
    try:
        pacing.mark_profile("quota_test_profile")
        assert False, "should have raised QuotaExceededError"
    except errors.QuotaExceededError:
        pass


def test_reel_quota_exceeded():
    pacing.reset("quota_test_reel")
    for _ in range(200):
        pacing.mark_reel_load("quota_test_reel")
    try:
        pacing.mark_reel_load("quota_test_reel")
        assert False, "should have raised QuotaExceededError"
    except errors.QuotaExceededError:
        pass


def test_surface_blocked():
    pacing.reset("surface_test")
    assert not pacing.is_surface_blocked("surface_test", "ig_profile")
    pacing.mark_surface_blocked("surface_test", "ig_profile")
    assert pacing.is_surface_blocked("surface_test", "ig_profile")
    assert not pacing.is_surface_blocked("surface_test", "google")


def test_reset_clears_counters():
    pacing.reset("reset_test")
    pacing.mark_profile("reset_test")
    pacing.reset("reset_test")
    assert pacing.quota_snapshot("reset_test")["profiles_used"] == 0
