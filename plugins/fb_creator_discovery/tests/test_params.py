"""Unit tests for parameter normalization helpers."""

from __future__ import annotations

from plugins.fb_creator_discovery._internal.params import (
    build_insights_field,
    build_metric_filter,
    coerce_limit,
    coerce_offset,
    csv,
    merge_fields,
    validate_time_range,
)


def test_csv_handles_none_string_and_iterables():
    assert csv(None) is None
    assert csv("") is None
    assert csv(" hi ") == "hi"
    assert csv(["a", "", "b ", " "]) == "a,b"
    assert csv(("x", "y")) == "x,y"


def test_coerce_limit_clamps_and_defaults():
    assert coerce_limit("10") == 10
    assert coerce_limit("nope", default=5) == 5
    assert coerce_limit(0) == 1  # min clamp
    assert coerce_limit(99999, maximum=100) == 100


def test_coerce_offset_clamps_negative():
    assert coerce_offset("-3") == 0
    assert coerce_offset("12") == 12
    assert coerce_offset(None, default=4) == 4


def test_validate_time_range_allowlist():
    assert validate_time_range("l28") == "L28"
    assert validate_time_range("L7") == "L7"
    assert validate_time_range("L99") is None
    assert validate_time_range(None) is None


def test_build_metric_filter_renders_subparams_correctly():
    out = build_metric_filter(min_value=1, max_value=50, time_range="L28", breakdown="follower")
    assert out == '{min:1,max:50,time_range:"L28",breakdown:"follower"}'


def test_build_metric_filter_partial_and_invalid():
    assert build_metric_filter(min_value=100000) == "{min:100000}"
    assert build_metric_filter() is None
    # invalid breakdown is dropped silently
    assert build_metric_filter(min_value=1, breakdown="bogus") == "{min:1}"
    # invalid time_range is dropped silently
    assert build_metric_filter(min_value=1, time_range="L77") == "{min:1}"


def test_build_metric_filter_float_format():
    assert build_metric_filter(min_value=1.5, time_range="L14") == '{min:1.5,time_range:"L14"}'


def test_build_insights_field():
    assert build_insights_field(["creator_reach", "creator_interactions"]) == (
        "insights.metrics(creator_reach,creator_interactions)"
    )
    assert (
        build_insights_field(["creator_reach"], period="day", time_range="L90")
        == "insights.metrics(creator_reach).period(day).time_range(L90)"
    )
    assert build_insights_field(None) is None
    assert build_insights_field([]) is None


def test_merge_fields_dedups_preserves_order():
    assert merge_fields("a,b", ["b", "c"], None, "d") == "a,b,c,d"
    assert merge_fields(None, "") is None
